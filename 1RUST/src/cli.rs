//! CLI: AutoRecoveryJpeg-cli <input> [out|-] [auto_color Y/N] [byte_enc] [ext] [-r]
use auto_recovery_jpeg::repair::{self, Config, DEFAULT_SKIP};
use std::{path::PathBuf, process::ExitCode};

const USAGE: &str = "Dùng: AutoRecoveryJpeg-cli <input> [out|-] [auto_color Y/N] [byte_enc] [ext] [-r]\n  out mặc định <input>/Repaired ('-' = mặc định) | byte_enc mặc định 153605 | ext mặc định jpg | -r quét thư mục con";

fn main() -> ExitCode {
    let mut a: Vec<String> = std::env::args().skip(1).collect();
    let recursive = a.iter().any(|s| s == "-r" || s == "--recursive");
    a.retain(|s| s != "-r" && s != "--recursive");
    if a.is_empty() || a[0] == "-h" || a[0] == "--help" {
        eprintln!("{USAGE}");
        return ExitCode::from(2);
    }
    let input = PathBuf::from(&a[0]);
    if !input.is_dir() {
        eprintln!("Không phải thư mục: {}", input.display());
        return ExitCode::from(2);
    }
    let cfg = Config {
        input,
        output: a.get(1).filter(|s| !s.is_empty() && *s != "-").map(PathBuf::from),
        auto_color: a.get(2).is_some_and(|s| s.eq_ignore_ascii_case("y")),
        skip: a.get(3).and_then(|s| s.parse().ok()).unwrap_or(DEFAULT_SKIP),
        ext: a.get(4).cloned().unwrap_or_else(|| "jpg".into()),
        recursive,
    };

    let mut fails = 0usize;
    let res = repair::run(
        &cfg,
        |i, n, name| println!("[{i}/{n}] {name}"),
        |_, _, _, r| match r {
            Ok(p) => println!("  OK  {}", p.display()),
            Err(e) => {
                fails += 1;
                eprintln!("  NO  {e:#}");
            }
        },
    );
    match res {
        Ok(0) => {
            eprintln!("No file");
            ExitCode::from(1)
        }
        Ok(n) => {
            println!("Xong: {} OK, {fails} lỗi / {n}", n - fails);
            ExitCode::from(u8::from(fails > 0))
        }
        Err(e) => {
            eprintln!("{e:#}");
            ExitCode::from(1)
        }
    }
}
