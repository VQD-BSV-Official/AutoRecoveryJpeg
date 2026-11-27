# AutoRecoveryJpeg v1.1.0 (Rust + Slint)

```
AutoRecoveryJpeg/
├─ Cargo.toml · build.rs
├─ ui/app.slint            # giao diện
├─ src/{main,repair,color,consts}.rs
└─ tool/                   # chép jpegsize.exe, mcudecode.exe, jpgfix2.exe vào đây
```

Build: `cargo build --release`, rồi đặt thư mục `tool/` cạnh `target/release/auto_recovery_jpeg.exe`
(khi `cargo run` thì tool/ nằm ở thư mục project).
