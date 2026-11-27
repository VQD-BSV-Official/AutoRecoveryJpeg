fn main() {
    // "fluent-light" = giao diện sáng, khớp thiết kế "Slint (light)"
    let cfg = slint_build::CompilerConfiguration::new().with_style("fluent-light".into());
    slint_build::compile_with_config("ui/app.slint", cfg).expect("compile slint");

    let mut res = winres::WindowsResource::new();


    res.set("FileDescription", "Auto Recovery JPEG Tool");
    res.set("ProductName", "AutoRecoveryJpeg");
    res.set("FileVersion", "1.1.0.0");
    res.set("ProductVersion", "1.1.0.0");
    res.set("LegalCopyright", "Copyright (c) 2026 Vũ Quang Đại");
    res.set("Comments", "Author: Vũ Quang Đại | Email: QDai.Dev08@gmail.com");

    res.compile().expect("Resource compilation failed");
}

