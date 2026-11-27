//! Port của main_cmd.py. Các bước giữ nguyên thứ tự:
//! s1 cắt chunk -> s2 lấy width -> s4 thử 4 kiểu sampling (21,22,11,41) -> decode lấy height -> fix MCU.
//! Mỗi file chạy trong một thư mục tạm riêng (cwd của tool) nên không còn đụng file tạm giữa các lần chạy.

use crate::color::auto_color;
use crate::consts::{COPYRIGHT_HEX, HEADER_TPL};
use anyhow::{anyhow, bail, Context, Result};
use regex::Regex;
use std::{
    fs::{self, File},
    io::{Read, Seek, SeekFrom},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicUsize, Ordering},
        LazyLock,
    },
};
use walkdir::WalkDir;

const CHUNK: u64 = 524_288;
const SAMPLINGS: [&str; 4] = ["21", "22", "11", "41"];
const DEFAULT_HEIGHT: u16 = 320;
pub const DEFAULT_SKIP: u64 = 153_605;

static RE_SIZE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(\d+)x(\d+)").unwrap());
static RE_MCU_PX: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"MCU:.*?\((\d+)x(\d+)\s*pixel\)").unwrap());
static RE_MCU_XY: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\((\d+),(\d+)\)").unwrap());

#[derive(Clone, Debug)]
pub struct Config {
    pub input: PathBuf,
    /// None => `<input>/Repaired`
    pub output: Option<PathBuf>,
    pub ext: String,
    pub skip: u64,
    pub auto_color: bool,
    pub recursive: bool,
}

struct Tools {
    jpegsize: PathBuf,
    mcudecode: PathBuf,
    jpgfix2: PathBuf,
}

impl Tools {
    fn locate() -> Result<Self> {
        let exe = if cfg!(windows) { ".exe" } else { "" };
        let mut cands: Vec<PathBuf> = Vec::new();
        if let Ok(p) = std::env::current_exe() {
            if let Some(d) = p.parent() {
                cands.push(d.join("tool"));
            }
        }
        cands.push(std::env::current_dir()?.join("tool"));
        for dir in cands {
            let t = Tools {
                jpegsize: dir.join(format!("jpegsize{exe}")),
                mcudecode: dir.join(format!("mcudecode{exe}")),
                jpgfix2: dir.join(format!("jpgfix2{exe}")),
            };
            if t.jpegsize.exists() && t.mcudecode.exists() && t.jpgfix2.exists() {
                return Ok(t);
            }
        }
        bail!("Không tìm thấy thư mục tool/ (jpegsize, mcudecode, jpgfix2) cạnh file chạy")
    }
}

/// Thư mục tạm, tự xoá khi drop.
struct TempDir(PathBuf);
impl TempDir {
    fn new() -> Result<Self> {
        static N: AtomicUsize = AtomicUsize::new(0);
        let p = std::env::temp_dir().join(format!(
            "auto_recovery_jpeg_{}_{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&p)?;
        Ok(Self(p))
    }
}
impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// Chạy tool ẩn cửa sổ, cwd = thư mục tạm. Trả (thành công?, stdout).
fn run_hidden(exe: &Path, args: &[&str], cwd: &Path) -> Result<(bool, String)> {
    let mut c = Command::new(exe);
    c.args(args)
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        c.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let o = c.output().with_context(|| format!("không chạy được {}", exe.display()))?;
    Ok((o.status.success(), String::from_utf8_lossy(&o.stdout).into_owned()))
}

fn last_line(s: &str, fallback: &str) -> String {
    s.trim().lines().last().unwrap_or(fallback).to_string()
}

// ───────────────────────── Step 1: cắt chunk ─────────────────────────
fn s1_cut_size(src: &Path, skip: u64, dst: &Path) -> Result<()> {
    let size = fs::metadata(src)?.len();
    let n = size.saturating_sub(skip).min(CHUNK);
    if n == 0 {
        bail!("Part size ({skip} B) lớn hơn kích thước file ({size} B)");
    }
    let mut f = File::open(src)?;
    f.seek(SeekFrom::Start(skip))?;
    let mut buf = vec![0u8; n as usize];
    f.read_exact(&mut buf)?;
    fs::write(dst, buf)?;
    Ok(())
}

// ───────────────────────── Step 2: lấy width ─────────────────────────
fn s2_get_width(tools: &Tools, tmp: &Path) -> Result<u16> {
    let (_, out) = run_hidden(&tools.jpegsize, &["temp_rp"], tmp)?;
    let w: u32 = RE_SIZE
        .captures(&out)
        .and_then(|m| m[1].parse().ok())
        .unwrap_or(0);
    if (1..65536).contains(&w) {
        Ok(w as u16)
    } else {
        bail!("Không đọc được width")
    }
}

// ───────────────────────── Header ─────────────────────────
fn header(height: u16, width: u16, sampling: &str) -> Result<Vec<u8>> {
    let hw = format!("{height:04x}{width:04x}");
    let s = HEADER_TPL.replace("FFFFFFFF", &hw).replace("DD", sampling);
    Ok(hex::decode(s)?)
}

fn read_head(p: &Path, n: usize) -> Result<Vec<u8>> {
    let mut buf = Vec::new();
    File::open(p)?.take(n as u64).read_to_end(&mut buf)?;
    Ok(buf)
}

fn read_tail(p: &Path, n: u64) -> Result<Vec<u8>> {
    let mut f = File::open(p)?;
    let len = f.metadata()?.len();
    f.seek(SeekFrom::Start(len.saturating_sub(n)))?;
    let mut buf = Vec::new();
    f.read_to_end(&mut buf)?;
    Ok(buf)
}

// ───────────────────────── Step 5: decode -> height thật ─────────────────────────
fn s5_decode_height(tools: &Tools, data: &[u8], tmp: &Path) -> Result<u32> {
    fs::write(tmp.join("temp_dec"), data)?;
    fs::write(tmp.join("Log_MCUs.txt"), b"")?; // xoá log cũ
    let (ok, out) = run_hidden(&tools.mcudecode, &["temp_dec", "Log_MCUs.txt", "--adv"], tmp)?;
    if !ok {
        bail!("{}", last_line(&out, "decode error"));
    }
    let log = tmp.join("Log_MCUs.txt");

    // Kích thước 1 MCU, vd "MCU: ... (16x8 pixel)"
    let head = String::from_utf8_lossy(&read_head(&log, 1024)?).into_owned();
    let m = RE_MCU_PX.captures(&head).ok_or_else(|| anyhow!("Log thiếu kích thước MCU"))?;
    let mcu_y: u32 = m[2].parse()?;

    // MCU cuối cùng, vd (303, 440) -> 440 hàng * mcu_y
    let tail = String::from_utf8_lossy(&read_tail(&log, 4096)?).into_owned();
    let last = RE_MCU_XY
        .captures_iter(&tail)
        .last()
        .ok_or_else(|| anyhow!("Log thiếu toạ độ MCU cuối"))?;
    let rows: u32 = last[2].parse()?;
    Ok(mcu_y * rows)
}

// ───────────────────────── jpgfix2: sync byte + shift MCU ─────────────────────────
fn fix(tools: &Tools, data: &[u8], thumb_ref: Option<&Path>, tmp: &Path) -> Result<Vec<u8>> {
    let (fin, fout) = (tmp.join("temp_fix_in.jpg"), tmp.join("temp_fix_out.jpg"));
    let _ = fs::remove_file(&fout);
    fs::write(&fin, data)?;
    let ref_arg = thumb_ref.map(|r| format!("ref={}", r.display()));
    let mut args = vec!["temp_fix_in.jpg", "temp_fix_out.jpg", "win=256", "skip=0"];
    if let Some(r) = ref_arg.as_deref() {
        args.push(r);
    }
    let (ok, out) = run_hidden(&tools.jpgfix2, &args, tmp)?;
    if !ok || !fout.exists() {
        bail!("fix error: {}", last_line(&out, "?"));
    }
    Ok(fs::read(fout)?)
}

// ───────────────────────── Step 4: thử từng sampling ─────────────────────────
fn s4_try(
    tools: &Tools, data: &[u8], width: u16, sampling: &str, thumb_ref: Option<&Path>, tmp: &Path,
) -> Result<Vec<u8>> {
    let with_header = |h: u16| -> Result<Vec<u8>> {
        let hd = header(h, width, sampling)?;
        let mut v = Vec::with_capacity(hd.len() + data.len());
        v.extend_from_slice(&hd);
        v.extend_from_slice(data);
        Ok(v)
    };
    let height_real = s5_decode_height(tools, &with_header(DEFAULT_HEIGHT)?, tmp)?;

    // +20 bù MCU cuối dở dang
    let h = u16::try_from(height_real + 20).map_err(|_| anyhow!("height {height_real} quá lớn"))?;
    let fixed = fix(tools, &with_header(h)?, thumb_ref, tmp)?;
    if fixed.len() < 2 {
        bail!("file fix rỗng");
    }
    // Thêm bản quyền
    let mut out = hex::decode(COPYRIGHT_HEX)?;
    out.extend_from_slice(&fixed[2..]);
    Ok(out)
}

fn s4_replace_header(
    tools: &Tools, src: &Path, width: u16, skip: u64, out_dir: &Path, out_name: &str,
    thumb_ref: Option<&Path>, tmp: &Path,
) -> Result<PathBuf> {
    let all = fs::read(src)?;
    let body = all.get(skip as usize..).unwrap_or(&[]);
    let mut last_err = anyhow!("không thử được sampling nào");
    for s in SAMPLINGS {
        match s4_try(tools, body, width, s, thumb_ref, tmp) {
            Ok(data) => {
                let path = out_dir.join(format!("{out_name}_R.JPG"));
                fs::write(&path, data)?;
                return Ok(path);
            }
            Err(e) => last_err = e.context(format!("sampling {s}")),
        }
    }
    Err(last_err)
}

/// "abc.jpg.nppp" -> "abc": cắt tại lần ".ext" đầu tiên (không phân biệt hoa/thường).
/// Không thấy ".ext" thì dùng tên bỏ đuôi cuối.
fn base_name(file: &Path, ext: &str) -> String {
    let name = file.file_name().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    let needle = format!(".{}", ext.trim().trim_start_matches('.').to_ascii_lowercase());
    match name.to_ascii_lowercase().find(&needle) {
        Some(i) if i > 0 => name[..i].to_string(),
        _ => file.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or(name),
    }
}

// ───────────────────────── Thumbnail ─────────────────────────
/// Thumbnail nằm trong thư mục tạm. Trả (đường dẫn thumbnail dùng làm ref, đường dẫn file "_v2" đã cắt bỏ thumbnail).
fn check_thumbnail(p: &Path, base: &str, tmp: &Path) -> Result<Option<(PathBuf, PathBuf)>> {
    let all = fs::read(p)?;
    let d = all.get(9..).unwrap_or(&[]);
    let Some(pos) = d
        .windows(4)
        .position(|w| w[0] == 0xFF && w[1] == 0xDB && w[2] == 0x00 && (w[3] == 0x84 || w[3] == 0x43))
    else {
        return Ok(None);
    };
    let thumb = tmp.join(format!("{base} (thumbnail).JPG"));
    let mut t = hex::decode(COPYRIGHT_HEX)?;
    t.extend_from_slice(&d[pos..]);
    fs::write(&thumb, t)?;
    let v2 = tmp.join("v2.JPG");
    fs::write(&v2, &d[..pos])?;
    Ok(Some((thumb, v2)))
}

// ───────────────────────── 1 file ─────────────────────────
fn run_one(tools: &Tools, cfg: &Config, file: &Path, out_dir: &Path) -> Result<PathBuf> {
    fs::create_dir_all(out_dir)?;
    let tmp = TempDir::new()?;
    let name = base_name(file, &cfg.ext);

    let (src, thumb) = match check_thumbnail(file, &name, &tmp.0)? {
        Some((t, v2)) => (v2, Some(t)),
        None => (file.to_path_buf(), None),
    };
    s1_cut_size(&src, cfg.skip, &tmp.0.join("temp_rp"))?;
    let width = s2_get_width(tools, &tmp.0)?;
    let out = s4_replace_header(tools, &src, width, cfg.skip, out_dir, &name, thumb.as_deref(), &tmp.0)?;

    // TODO(color_mcu): bản Python chưa có logic này (chỉ có tham số off=dY,dCb,dCr của jpgfix2).
    if cfg.auto_color {
        auto_color(&out)?;
    }
    // Thumbnail chỉ ra thư mục output khi repair thành công
    if let Some(t) = &thumb {
        fs::copy(t, out_dir.join(t.file_name().unwrap()))?;
    }
    Ok(out)
}

fn collect_files(cfg: &Config, out_dir: &Path) -> Vec<PathBuf> {
    let needle = format!(".{}", cfg.ext.trim().trim_start_matches('.').to_ascii_lowercase());
    let depth = if cfg.recursive { usize::MAX } else { 1 };
    let mut v: Vec<PathBuf> = WalkDir::new(&cfg.input)
        .max_depth(depth)
        .into_iter()
        // không quét lại thư mục output (mặc định nằm trong input)
        .filter_entry(|e| {
            !(e.depth() > 0
                && e.file_type().is_dir()
                && std::path::absolute(e.path()).is_ok_and(|p| p == out_dir))
        })
        .filter_map(Result::ok)
        // Như bản Python: tên chứa ".jpg" ở bất kỳ đâu, vd "abc.jpg.nppp"
        .filter(|e| {
            e.file_type().is_file()
                && e.file_name().to_string_lossy().to_ascii_lowercase().contains(&needle)
        })
        .map(|e| e.into_path())
        .collect();
    v.sort();
    v
}

/// Chạy cả batch. `on_begin(i, n, name)` trước mỗi file, `on_done(i, n, name, kết quả)` sau mỗi file.
/// Một file lỗi không làm dừng batch.
pub fn run(
    cfg: &Config,
    mut on_begin: impl FnMut(usize, usize, &str),
    mut on_done: impl FnMut(usize, usize, &str, Result<PathBuf>),
) -> Result<usize> {
    let tools = Tools::locate()?;
    let out_root = std::path::absolute(cfg.output.clone().unwrap_or_else(|| cfg.input.join("Repaired")))?;
    let files = collect_files(cfg, &out_root);
    let n = files.len();

    for (i, file) in files.iter().enumerate() {
        let name = file.file_name().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
        on_begin(i + 1, n, &name);

        // Giữ cấu trúc thư mục con khi quét đệ quy
        let out_dir = match file.parent().and_then(|p| p.strip_prefix(&cfg.input).ok()) {
            Some(rel) if cfg.recursive => out_root.join(rel),
            _ => out_root.clone(),
        };
        let res = run_one(&tools, cfg, file, &out_dir);
        on_done(i + 1, n, &name, res);
    }
    Ok(n)
}
