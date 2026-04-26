#!/usr/bin/env python3
"""
LAB color transfer for inspiration-driven style preprocessing.

Implements Reinhard et al. "Color Transfer between Images" (2001) in pure numpy.
Transfers the color palette from an inspiration image onto a subject image
while perfectly preserving the subject's composition and edge structure.
"""

import numpy as np
from PIL import Image


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Convert RGB float [0,1] array to LAB color space."""
    # Linearize sRGB
    linear = np.where(rgb > 0.04045, ((rgb + 0.055) / 1.055) ** 2.4, rgb / 12.92)

    r, g, b = linear[..., 0], linear[..., 1], linear[..., 2]
    # sRGB D65 matrix
    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b

    # Normalize by D65 white point
    x /= 0.95047
    z /= 1.08883

    delta = 6.0 / 29.0

    def f(t):
        return np.where(t > delta ** 3, t ** (1.0 / 3.0), t / (3 * delta ** 2) + 4.0 / 29.0)

    fx, fy, fz = f(x), f(y), f(z)
    L = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b_ch = 200.0 * (fy - fz)
    return np.stack([L, a, b_ch], axis=-1)


def _lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    """Convert LAB array back to RGB uint8."""
    L, a, b_ch = lab[..., 0], lab[..., 1], lab[..., 2]

    fy = (L + 16.0) / 116.0
    fx = a / 500.0 + fy
    fz = fy - b_ch / 200.0

    delta = 6.0 / 29.0
    x = np.where(fx > delta, fx ** 3, 3 * delta ** 2 * (fx - 4.0 / 29.0)) * 0.95047
    y = np.where(fy > delta, fy ** 3, 3 * delta ** 2 * (fy - 4.0 / 29.0))
    z = np.where(fz > delta, fz ** 3, 3 * delta ** 2 * (fz - 4.0 / 29.0)) * 1.08883

    r = 3.2404542 * x - 1.5371385 * y - 0.4985314 * z
    g = -0.9692660 * x + 1.8760108 * y + 0.0415560 * z
    b = 0.0556434 * x - 0.2040259 * y + 1.0572252 * z

    rgb = np.clip(np.stack([r, g, b], axis=-1), 0, None)
    srgb = np.where(rgb > 0.0031308, 1.055 * rgb ** (1.0 / 2.4) - 0.055, 12.92 * rgb)
    return np.clip(srgb * 255.0, 0, 255).astype(np.uint8)


def lab_color_transfer(subject: Image.Image, inspiration: Image.Image,
                       strength: float = 0.7) -> Image.Image:
    """Transfer color palette from inspiration onto subject using LAB space.

    Remaps the subject's color distribution (mean + stddev per LAB channel)
    to match the inspiration's. Preserves all spatial structure — only
    statistics change. Luminance is transferred at reduced intensity to
    preserve the subject's lighting/depth.

    Args:
        subject: Image whose composition to keep (e.g. Scryfall card art)
        inspiration: Image whose color palette to adopt (e.g. style reference poster)
        strength: 0.0 = no transfer, 1.0 = full transfer. Default 0.7.

    Returns:
        Subject image with inspiration's color palette applied.
    """
    src = np.array(subject, dtype=np.float64) / 255.0
    ref = np.array(inspiration.resize(subject.size), dtype=np.float64) / 255.0

    src_lab = _rgb_to_lab(src)
    ref_lab = _rgb_to_lab(ref)

    result_lab = np.empty_like(src_lab)

    for ch in range(3):
        s_mean = src_lab[..., ch].mean()
        s_std = src_lab[..., ch].std()
        r_mean = ref_lab[..., ch].mean()
        r_std = ref_lab[..., ch].std()

        if s_std < 1e-6:
            result_lab[..., ch] = r_mean
        else:
            transferred = (src_lab[..., ch] - s_mean) * (r_std / s_std) + r_mean
            # Luminance (ch 0): transfer at half strength to preserve depth/lighting
            ch_strength = strength * 0.5 if ch == 0 else strength
            result_lab[..., ch] = (1 - ch_strength) * src_lab[..., ch] + ch_strength * transferred

    result_lab[..., 0] = np.clip(result_lab[..., 0], 0, 100)
    result_lab[..., 1] = np.clip(result_lab[..., 1], -128, 127)
    result_lab[..., 2] = np.clip(result_lab[..., 2], -128, 127)

    return Image.fromarray(_lab_to_rgb(result_lab))
