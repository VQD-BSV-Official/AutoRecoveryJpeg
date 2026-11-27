// Ẩn cửa sổ console khi build release trên Windows
#![cfg_attr(all(not(debug_assertions), windows), windows_subsystem = "windows")]

use auto_recovery_jpeg::repair;
use image::imageops::FilterType;
use slint::{Model, ModelRc, Rgba8Pixel, SharedPixelBuffer, VecModel, Weak};
use std::{
    panic::{catch_unwind, AssertUnwindSafe},
    path::{Path, PathBuf},
    rc::Rc,
};

slint::include_modules!();

const THUMB: u32 = 256; // khớp iconSize của bản Qt

fn main() -> Result<(), slint::PlatformError> {
    let ui = AppWindow::new()?;

    // Đợi Slint tạo và hiển thị cửa sổ xong rồi mới lấy kích thước thực tế để căn giữa.
    #[cfg(target_os = "windows")]
    {
        let weak = ui.as_weak();
        slint::Timer::single_shot(std::time::Duration::from_millis(150), move || {
            if let Some(ui) = weak.upgrade() {
                center_window(&ui);
            }
        });
    }

    ui.set_log(ModelRc::from(Rc::new(VecModel::<LogLine>::default())));
    ui.set_thumbs(ModelRc::from(Rc::new(VecModel::<Thumb>::default())));

    // Chọn thư mục (nút "input" / "output")
    {
        let w = ui.as_weak();
        ui.on_pick_input(move || {
            let ui = w.unwrap();
            if let Some(p) = rfd::FileDialog::new().set_title("Select Folder").pick_folder() {
                ui.set_input_path(p.display().to_string().into());
                ui.set_output_path(p.join("Repaired").display().to_string().into());
            }
        });
        let w = ui.as_weak();
        ui.on_pick_output(move || {
            let ui = w.unwrap();
            if let Some(p) = rfd::FileDialog::new().set_title("Select Folder").pick_folder() {
                ui.set_output_path(p.display().to_string().into());
            }
        });
        // Nút "..." mở thư mục trong trình quản lý file
        let w = ui.as_weak();
        ui.on_open_input(move || open_path(&w.unwrap().get_input_path()));
        let w = ui.as_weak();
        ui.on_open_output(move || open_path(&w.unwrap().get_output_path()));
        ui.on_open_file(|p| open_path(&p));
    }

    // START
    {
        let w = ui.as_weak();
        ui.on_start(move || {
            let ui = w.unwrap();
            let input = ui.get_input_path().to_string();
            if !Path::new(&input).is_dir() {
                return;
            }
            let out = ui.get_output_path().to_string();
            let cfg = repair::Config {
                input: PathBuf::from(input),
                output: (!out.trim().is_empty()).then(|| PathBuf::from(out)),
                ext: ui.get_ext().to_string(),
                skip: ui.get_part_size().trim().parse().unwrap_or(repair::DEFAULT_SKIP),
                auto_color: ui.get_opt_color(),
                recursive: ui.get_opt_sub(),
            };

            clear::<LogLine>(&ui.get_log());
            clear::<Thumb>(&ui.get_thumbs());
            ui.set_done(0);
            ui.set_total(0);
            ui.set_running(true); // nút START tự khoá (active: !running)
            push_log(&ui, "", "START", false);

            let weak = ui.as_weak();
            std::thread::spawn(move || worker(cfg, weak));
        });
    }

    ui.run()
}


// Căn cửa sổ Slint vào giữa màn hình chính Windows.
// Dùng Win32 API trực tiếp nên không cần thêm dependency vào Cargo.toml.
#[cfg(target_os = "windows")]
fn center_window(ui: &AppWindow) {
    #[link(name = "user32")]
    unsafe extern "system" {
        fn GetSystemMetrics(index: i32) -> i32;
    }

    const SM_CXSCREEN: i32 = 0;
    const SM_CYSCREEN: i32 = 1;

    let window_size = ui.window().size();

    let (screen_width, screen_height) = unsafe {
        (
            GetSystemMetrics(SM_CXSCREEN),
            GetSystemMetrics(SM_CYSCREEN),
        )
    };

    let x = ((screen_width - window_size.width as i32) / 2).max(0);
    let y = ((screen_height - window_size.height as i32) / 2).max(0);

    ui.window()
        .set_position(slint::PhysicalPosition::new(x, y));
}

// ───────────────────────── Worker thread ─────────────────────────
fn worker(cfg: repair::Config, w: Weak<AppWindow>) {
    let (w_begin, w_done, w_end) = (w.clone(), w.clone(), w);

    let result = catch_unwind(AssertUnwindSafe(|| {
        repair::run(
            &cfg,
            |_i, n, _name| {
                let _ = w_begin.upgrade_in_event_loop(move |ui| ui.set_total(n as i32));
            },
            |i, _n, name, res| {
                let name = name.to_string();
                let (out, err) = match res {
                    Ok(p) => (Some(p), None),
                    Err(e) => (None, Some(format!("{e:#}"))),
                };
                // Scale ở thread phụ, UI thread chỉ việc tạo Image
                let buf = out.as_deref().and_then(make_thumb);
                let _ = w_done.upgrade_in_event_loop(move |ui| {
                    ui.set_done(i as i32);
                    match (&out, err) {
                        (Some(p), _) => {
                            let img = buf.map(slint::Image::from_rgba8).unwrap_or_default();
                            push(&ui.get_thumbs(), Thumb {
                                img,
                                name: name.clone().into(),
                                path: p.display().to_string().into(),
                            });
                            push_log(&ui, "OK...", &name, false);
                        }
                        (None, e) => {
                            push_log(&ui, "NO...", &name, false);
                            if let Some(e) = e {
                                push_log(&ui, "", &format!("└─ {e}"), true);
                            }
                        }
                    }
                });
            },
        )
    }));

    let msg = match result {
        Ok(Ok(0)) => Some("No file".to_string()),
        Ok(Ok(_)) => None,
        Ok(Err(e)) => Some(format!("{e:#}")),
        Err(_) => Some("Internal error (panic)".to_string()),
    };
    let _ = w_end.upgrade_in_event_loop(move |ui| {
        if let Some(m) = msg {
            push_log(&ui, "NO...", &m, false);
        }
        ui.set_running(false);
    });
}

fn make_thumb(p: &Path) -> Option<SharedPixelBuffer<Rgba8Pixel>> {
    let img = image::open(p).ok()?.resize(THUMB, THUMB, FilterType::Triangle).to_rgba8();
    Some(SharedPixelBuffer::clone_from_slice(img.as_raw(), img.width(), img.height()))
}

// ───────────────────────── Tiện ích model ─────────────────────────
fn push<T: Clone + 'static>(model: &ModelRc<T>, item: T) {
    if let Some(vm) = model.as_any().downcast_ref::<VecModel<T>>() {
        vm.push(item);
    }
}

fn clear<T: Clone + 'static>(model: &ModelRc<T>) {
    if let Some(vm) = model.as_any().downcast_ref::<VecModel<T>>() {
        vm.set_vec(Vec::new());
    }
}

fn push_log(ui: &AppWindow, status: &str, text: &str, dim: bool) {
    push(&ui.get_log(), LogLine { status: status.into(), text: text.into(), dim });
}

/// Mở thư mục / file bằng ứng dụng mặc định của hệ điều hành.
fn open_path(p: &str) {
    if p.trim().is_empty() || !Path::new(p).exists() {
        return;
    }
    let prog = if cfg!(windows) {
        "explorer"
    } else if cfg!(target_os = "macos") {
        "open"
    } else {
        "xdg-open"
    };
    let _ = std::process::Command::new(prog).arg(p).spawn();
}
