//! Thay thế đoạn `auto_color` (PIL + numpy) của bản Python.
//! Thuật toán giữ nguyên: (1) kéo giãn từng kênh theo percentile 1–99%,
//! (2) cân bằng gray-world, (3) lưu JPEG quality 99 và giữ lại EXIF.
//! Làm bằng histogram + bảng tra (LUT) nên rất nhanh, không cần cấp phát float cho cả ảnh.

use anyhow::{Context, Result};
use image::{codecs::jpeg::JpegEncoder, ExtendedColorType, ImageFormat};
use std::path::Path;

/// Giá trị percentile `p` (0..100) theo kiểu nội suy tuyến tính của numpy, tính từ histogram.
fn percentile(hist: &[u64; 256], n: u64, p: f64) -> f64 {
    let pos = p / 100.0 * (n.saturating_sub(1)) as f64;
    let (lo_i, hi_i) = (pos.floor() as u64, pos.ceil() as u64);
    let kth = |k: u64| -> f64 {
        let mut cum = 0u64;
        for (v, &c) in hist.iter().enumerate() {
            cum += c;
            if cum > k {
                return v as f64;
            }
        }
        255.0
    };
    let (a, b) = (kth(lo_i), kth(hi_i));
    a + (b - a) * (pos - lo_i as f64)
}

/// Lấy nguyên segment APP1 "Exif" của file JPEG gốc (nếu có).
fn extract_exif(b: &[u8]) -> Option<Vec<u8>> {
    let mut pos = 2;
    while pos + 4 <= b.len() && b[pos] == 0xFF {
        let marker = b[pos + 1];
        if marker == 0xDA {
            break;
        }
        let len = u16::from_be_bytes([b[pos + 2], b[pos + 3]]) as usize;
        let end = pos + 2 + len;
        if end > b.len() {
            break;
        }
        if marker == 0xE1 && b[pos + 4..end].starts_with(b"Exif\0\0") {
            return Some(b[pos..end].to_vec());
        }
        pos = end;
    }
    None
}

/// Chèn segment EXIF ngay sau APP0 (JFIF) của JPEG mới.
fn insert_exif(jpeg: Vec<u8>, exif: &[u8]) -> Vec<u8> {
    let mut at = 2;
    if jpeg.len() > 6 && jpeg[2] == 0xFF && jpeg[3] == 0xE0 {
        at = 2 + 2 + u16::from_be_bytes([jpeg[4], jpeg[5]]) as usize;
    }
    let at = at.min(jpeg.len());
    let mut out = Vec::with_capacity(jpeg.len() + exif.len());
    out.extend_from_slice(&jpeg[..at]);
    out.extend_from_slice(exif);
    out.extend_from_slice(&jpeg[at..]);
    out
}

/// Chỉnh màu tự động, ghi đè lên chính file `path`.
pub fn auto_color(path: &Path) -> Result<()> {
    let bytes = std::fs::read(path).with_context(|| format!("đọc {}", path.display()))?;
    let exif = extract_exif(&bytes);
    let img = image::load_from_memory_with_format(&bytes, ImageFormat::Jpeg)
        .context("giải mã JPEG để chỉnh màu")?
        .to_rgb8();
    let (w, h) = img.dimensions();
    let mut raw = img.into_raw();
    let n = w as u64 * h as u64;
    if n == 0 {
        return Ok(());
    }

    // Histogram từng kênh
    let mut hist = [[0u64; 256]; 3];
    for px in raw.chunks_exact(3) {
        for c in 0..3 {
            hist[c][px[c] as usize] += 1;
        }
    }

    // Bước 1: kéo giãn theo percentile 1–99
    let mut stretch = [[0f32; 256]; 3];
    let mut mean = [0f64; 3];
    for c in 0..3 {
        let lo = percentile(&hist[c], n, 1.0);
        let hi = percentile(&hist[c], n, 99.0);
        let mut sum = 0f64;
        let flat = hi - lo < 1.0; // kênh gần phẳng: giữ nguyên, tránh khuếch đại cực đại
        for v in 0..256 {
            let s = if flat { v as f64 } else { ((v as f64 - lo) / (hi - lo) * 255.0).clamp(0.0, 255.0) };
            stretch[c][v] = s as f32;
            sum += s * hist[c][v] as f64;
        }
        mean[c] = (sum / n as f64).max(1e-6);
    }

    // Bước 2: gray-world
    let gray = (mean[0] + mean[1] + mean[2]) / 3.0;
    let mut lut = [[0u8; 256]; 3];
    for c in 0..3 {
        let gain = (gray / mean[c]) as f32;
        for v in 0..256 {
            lut[c][v] = (stretch[c][v] * gain).clamp(0.0, 255.0) as u8;
        }
    }
    for px in raw.chunks_exact_mut(3) {
        for c in 0..3 {
            px[c] = lut[c][px[c] as usize];
        }
    }

    // Lưu quality 99 rồi gắn lại EXIF
    let mut out = Vec::new();
    JpegEncoder::new_with_quality(&mut out, 99).encode(&raw, w, h, ExtendedColorType::Rgb8)?;
    if let Some(e) = exif {
        out = insert_exif(out, &e);
    }
    std::fs::write(path, out)?;
    Ok(())
}
