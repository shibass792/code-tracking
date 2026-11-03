# ShiBass lesson video maker - faceless course videos:
#   background = auto slides from the lesson text, or your Cubase screen recording (OBS) when you have one
#   avatar     = a talking ShiBass character (mouth follows your voice) or your logo pulsing with the voice
#   + Hebrew subtitles + the lesson audio in your voice
#
# Usage:
#   python make-video.py <lesson.txt> <lesson.wav> [--screen rec.mp4] [--avatar logo.png] [--out video.mp4]
#   python make-video.py --course C:\ShiBass_Voice\courses\groove      (all lessons of a course)
# Screen recordings are picked up automatically from <course>_screen\<lesson-name>.mp4
import argparse
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H, FPS = 1920, 1080, 25
ACC, ACC2, BG1, BG2 = (139, 108, 255), (63, 208, 201), (13, 15, 20), (27, 32, 48)
FONT_CANDIDATES = [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


# ------------------------------------------------------------------ helpers
def ffmpeg_bin():
    f = shutil.which("ffmpeg")
    if f:
        return f
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg not found. Install it:  pip install imageio-ffmpeg")


def font(size):
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


try:
    from PIL import features as _feat
    RAQM = bool(_feat.check("raqm"))  # Pillow with libraqm does bidi itself
except Exception:
    RAQM = False


def rtl(s):
    """Visual order for Hebrew. If Pillow has raqm it already handles bidi - don't flip twice."""
    if RAQM:
        return s
    try:
        from bidi.algorithm import get_display
        return get_display(s)
    except ImportError:
        try:
            from bidi import get_display
            return get_display(s)
        except ImportError:
            return " ".join(w[::-1] if re.search(r"[\u0590-\u05FF]", w) else w for w in s.split()[::-1])


def wrap(text, fnt, max_w, draw):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(rtl(t), font=fnt) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def load_audio(path, ff):
    raw = subprocess.run([ff, "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0, 16000


def loudness_per_frame(a, sr, n_frames):
    hop = sr / FPS
    out = np.zeros(n_frames, np.float32)
    for i in range(n_frames):
        s = a[int(i * hop):int((i + 1) * hop)]
        out[i] = float(np.sqrt(np.mean(s * s))) if len(s) else 0.0
    peak = np.percentile(out, 95) or 1.0
    out = np.clip(out / peak, 0, 1)
    sm = np.copy(out)  # light smoothing so the mouth doesn't flicker
    for i in range(1, len(sm)):
        sm[i] = 0.55 * out[i] + 0.45 * sm[i - 1]
    return sm


# ------------------------------------------------------------------ text -> timeline
def parse_lesson(txt):
    text = Path(txt).read_text(encoding="utf-8-sig").strip()
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    title = Path(txt).stem
    title = re.sub(r"^\d+[-_ ]*", "", title).replace("-", " ").strip()
    return title, paras


def chunk(sent, limit=80):
    """Split a long sentence at commas/words so a subtitle never exceeds 2 lines."""
    if len(sent) <= limit:
        return [sent]
    parts, cur = [], ""
    for piece in re.split(r"(?<=,)\s+", sent):
        for w in piece.split():
            if cur and len(cur) + len(w) + 1 > limit:
                parts.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        if len(cur) > limit * 0.6:
            parts.append(cur)
            cur = ""
    if cur:
        parts.append(cur)
    return parts


def build_timeline(paras, total):
    """Each paragraph gets time in proportion to its length; inside, each sentence likewise."""
    weights = [len(p) + 25 for p in paras]
    scale = total / sum(weights)
    t, slides, subs = 0.0, [], []
    for p, w in zip(paras, weights):
        d = w * scale
        slides.append((t, t + d, p))
        sents = [c for s in re.split(r"(?<=[.!?:])\s+", p) if s.strip() for c in chunk(s.strip())]
        sw = [len(s) + 4 for s in sents]
        tt = t
        for s, x in zip(sents, sw):
            sd = d * x / sum(sw)
            subs.append((tt, tt + sd, s))
            tt += sd
        t += d
    return slides, subs


# ------------------------------------------------------------------ drawing
def gradient_bg():
    y = np.linspace(0, 1, H)[:, None]
    x = np.linspace(0, 1, W)[None, :]
    g = np.clip(0.65 * y + 0.35 * x, 0, 1)[..., None]
    img = (np.array(BG1) * (1 - g) + np.array(BG2) * g).astype(np.uint8)
    return Image.fromarray(img, "RGB")


def make_slide(course, title, para, idx, total, daw=True):
    img = gradient_bg()
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((90, 50, W - 90, 120), 22, fill=(21, 25, 34))
    f_small, f_title, f_body = font(32), font(54 if daw else 64), font(54)
    head = title if asset("logo.png") is not None else f"{course}  \u00b7  {title}"
    d.text((W - 130, 85), rtl(head), font=f_small, fill=(152, 161, 179), anchor="rm")
    lg = asset("logo.png")
    if lg is not None:
        lg = lg.copy()
        lg.thumbnail((220, 46))
        img.paste(lg, (124, 85 - lg.height // 2), lg)
        d.text((124 + lg.width + 24, 85), f"{idx}/{total}", font=f_small, fill=(152, 161, 179), anchor="lm")
    else:
        d.text((130, 85), f"{idx}/{total}", font=f_small, fill=(152, 161, 179), anchor="lm")
    first = re.split(r"(?<=[.!?:])\s+", para)[0]
    if daw:
        lines = wrap(first, f_title, W - 260, d)
        line = lines[0] + (" ..." if len(lines) > 1 else "")
        d.text((W - 110, 150), rtl(line), font=f_title, fill=(233, 236, 243), anchor="ra")
        return img
    lines = wrap(first, f_title, W - 520, d)[:3]
    y = 330
    for ln in lines:
        d.text((W - 160, y), rtl(ln), font=f_title, fill=(233, 236, 243), anchor="ra")
        y += 92
    d.rectangle((W - 160 - 220, y + 20, W - 160, y + 28), fill=ACC)
    rest = para[len(first):].strip()
    if rest:
        body = wrap(rest, f_body, W - 520, d)[:3]
        y += 70
        for ln in body:
            d.text((W - 160, y), rtl(ln), font=f_body, fill=(170, 178, 196), anchor="ra")
            y += 74
    return img


ASSETS = {}


def asset(name):
    """Optional brand images next to the avatar: logo.png, portrait.png (C:\\ShiBass_Voice)."""
    if name not in ASSETS:
        p = Path(ASSETS.get("_dir", ".")) / name
        im = Image.open(p).convert("RGBA") if p.exists() else None
        pdf = p.with_suffix(".pdf")
        if im is None and name == "logo.png" and pdf.exists():
            try:
                try:
                    import pymupdf as fitz
                except ImportError:
                    import fitz
                pix = fitz.open(str(pdf))[0].get_pixmap(dpi=300)
                im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("RGBA")
            except Exception as e:
                print(f"  (logo.pdf not readable: {e})")
        if im is not None and name == "logo.png" and np.asarray(im.getchannel("A")).min() > 250:
            # dark logo on white background -> white logo on transparent
            g = np.asarray(im.convert("L")).astype(float)
            a = Image.fromarray(np.clip((235 - g) * 1.6, 0, 255).astype(np.uint8))
            im = Image.new("RGBA", im.size, (255, 255, 255, 0))
            im.putalpha(a)
            im = im.crop(im.getbbox())
        ASSETS[name] = im
    return ASSETS[name]


def make_title_card(course, title):
    logo, por = asset("logo.png"), asset("portrait.png")
    if logo is not None or por is not None:
        img = gradient_bg().convert("RGBA")
        d = ImageDraw.Draw(img)
        cx = W // 2 + (230 if por is not None else 0)
        if por is not None:
            p = por.copy()
            p.thumbnail((760, 900))
            glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
            g = Image.new("RGBA", p.size, (*ACC, 0))
            g.putalpha(p.getchannel("A").point(lambda a: a // 2))
            glow.alpha_composite(g, (120, H - p.height))
            img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(30)))
            img.alpha_composite(p, (120, H - p.height))
        y = H // 2 - 170
        if logo is not None:
            lg = logo.copy()
            lg.thumbnail((640, 160))
            img.alpha_composite(lg, (cx - lg.width // 2, y - lg.height // 2))
            y += lg.height // 2 + 40
        else:
            d.text((cx, y), rtl(course), font=font(48), fill=ACC2, anchor="mm")
            y += 60
        d.text((cx, y + 70), rtl(title), font=font(84), fill=(240, 242, 248), anchor="mm")
        d.rectangle((cx - 140, y + 140, cx + 140, y + 148), fill=ACC)
        return img.convert("RGB")
    return _plain_title_card(course, title)


def _plain_title_card(course, title):
    img = gradient_bg()
    d = ImageDraw.Draw(img)
    d.text((W // 2, H // 2 - 70), rtl(course), font=font(48), fill=ACC2, anchor="mm")
    d.text((W // 2, H // 2 + 30), rtl(title), font=font(96), fill=(240, 242, 248), anchor="mm")
    d.rectangle((W // 2 - 140, H // 2 + 110, W // 2 + 140, H // 2 + 118), fill=ACC)
    return img


def subtitle_layer(text):
    f = font(46)
    tmp = Image.new("RGBA", (W, 260), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    lines = wrap(text, f, W - 620, d)[:2]
    hgt = 74 * len(lines) + 30
    y0 = 260 - hgt
    widest = max(d.textlength(rtl(ln), font=f) for ln in lines)
    cx = (460 + W - 60) // 2
    d.rounded_rectangle((cx - widest / 2 - 30, y0, cx + widest / 2 + 30, 250), 18, fill=(0, 0, 0, 170))
    y = y0 + 18
    for ln in lines:
        d.text((cx, y), rtl(ln), font=f, fill=(255, 255, 255), anchor="ma")
        y += 74
    return tmp



def shibass_dj(level, t, S):
    """Drawn ShiBass: bearded DJ with a bun, sunglasses and headphones. Mouth follows the voice."""
    k = 2  # draw at 2x, downscale for smooth edges
    Z = S * k
    im = Image.new("RGBA", (Z, Z), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    bob = int(5 * k * math.sin(t * 2.2)) - int(6 * k * level)
    cx = Z // 2
    skin, skin_d = (196, 140, 104), (170, 116, 84)
    hair, beard = (34, 28, 26), (62, 48, 40)
    # shoulders + white tee
    d.ellipse((cx - 300, Z - 190, cx + 300, Z + 260), fill=(236, 238, 242))
    d.polygon([(cx - 60, Z - 175), (cx + 60, Z - 175), (cx, Z - 110)], fill=skin_d)  # neckline
    d.text((cx, Z - 70), "SHIBASS", font=font(44), fill=(40, 44, 60), anchor="mm")
    # neck
    d.rectangle((cx - 52, Z - 260 + bob // 2, cx + 52, Z - 170), fill=skin_d)
    y = 120 + bob  # head top
    # bun + hair
    d.ellipse((cx - 46, y - 70, cx + 46, y + 14), fill=hair)
    d.ellipse((cx - 150, y, cx + 150, y + 200), fill=hair)
    # head
    d.ellipse((cx - 140, y + 30, cx + 140, y + 360), fill=skin)
    d.ellipse((cx - 160, y + 170, cx - 118, y + 240), fill=skin_d)  # ears
    d.ellipse((cx + 118, y + 170, cx + 160, y + 240), fill=skin_d)
    d.pieslice((cx - 142, y + 20, cx + 142, y + 200), 180, 360, fill=hair)  # hairline
    # beard
    d.chord((cx - 136, y + 150, cx + 136, y + 386), 0, 180, fill=beard)
    # mouth (opens with the voice) + mustache
    mh = int(8 + 46 * level)
    d.rounded_rectangle((cx - 42, y + 292, cx + 42, y + 292 + mh), 16, fill=(60, 22, 26))
    if mh > 24:
        d.rectangle((cx - 30, y + 294, cx + 30, y + 302), fill=(240, 240, 240))  # teeth
    d.chord((cx - 70, y + 255, cx + 70, y + 310), 180, 360, fill=hair)
    d.ellipse((cx - 14, y + 228, cx + 14, y + 262), fill=skin_d)  # nose
    # sunglasses (with a reflection that drifts)
    for sx in (-1, 1):
        x0 = cx + sx * 74
        d.rounded_rectangle((x0 - 62, y + 168, x0 + 62, y + 232), 26, fill=(18, 20, 28))
        rx = x0 - 40 + int(20 * math.sin(t * 0.7))
        d.line((rx, y + 182, rx + 26, y + 182), fill=(110, 120, 150), width=6)
    d.line((cx - 14, y + 186, cx + 14, y + 186), fill=(18, 20, 28), width=10)
    # headphones
    d.arc((cx - 176, y - 10, cx + 176, y + 330), 190, 350, fill=(30, 32, 44), width=26)
    d.arc((cx - 176, y - 10, cx + 176, y + 330), 200, 340, fill=ACC2, width=10)
    for sx in (-1, 1):
        x0 = cx + sx * 160
        d.rounded_rectangle((x0 - 34, y + 150, x0 + 34, y + 270), 22, fill=(30, 32, 44))
        d.rounded_rectangle((x0 - 22, y + 166, x0 + 22, y + 254), 14, fill=ACC2 if level < 0.5 else (255, 255, 255))
    return im.resize((S, S), Image.LANCZOS)


class Avatar:
    """ShiBass talking character (default) or a user logo that pulses with the voice."""
    S = 340

    def __init__(self, logo=None):
        self.logo = None
        self.cutout = None  # transparent PNG character -> free-standing, no circle
        if logo and Path(logo).exists():
            im = Image.open(logo).convert("RGBA")
            alpha = np.asarray(im.getchannel("A"))
            if (alpha < 250).mean() > 0.05:  # has real transparency
                im = im.crop(im.getbbox())
                big_h = im.height
                im.thumbnail((370, 385))
                self.cutout = im
                self.mouth = None
                meta = Path(logo).with_suffix(".json")
                if meta.exists():
                    import json
                    j = json.loads(meta.read_text())
                    if "mouth_frac" in j:  # fractions of the cropped image: x0, lip-line y, x1
                        fx0, fy, fx1 = j["mouth_frac"]
                        self.mouth = [fx0 * im.width, fy * im.height, fx1 * im.width]
                    else:
                        k = im.height / big_h
                        self.mouth = [v * k for v in j["mouth"]]
            else:
                self.logo = ImageOps.fit(im, (self.S - 24, self.S - 24))
        self.mask = Image.new("L", (self.S, self.S), 0)
        ImageDraw.Draw(self.mask).ellipse((0, 0, self.S - 1, self.S - 1), fill=255)

    def talk(self, im, level):
        """Jaw-drop lip sync: shift the chin under the lip line down and show a dark mouth gap."""
        if not self.mouth or level < 0.08:
            return im
        x0, y, x1 = self.mouth
        w = x1 - x0
        op = max(1, int(round(level * w * 0.28)))
        bx0, bx1, by1 = int(x0 - w * 0.35), int(x1 + w * 0.35), int(y + w * 1.25)
        y = int(y)
        out = im.copy()
        d = ImageDraw.Draw(out)
        d.ellipse((x0 + w * 0.08, y - 1, x1 - w * 0.08, y + op * 2), fill=(52, 18, 24, 255))
        chin = im.crop((bx0, y, bx1, by1))
        m = Image.new("L", chin.size, 0)
        ImageDraw.Draw(m).ellipse((0, -chin.height, chin.width - 1, chin.height - 1), fill=255)
        m = m.filter(ImageFilter.GaussianBlur(max(1, w * 0.08)))
        out.paste(chin, (bx0, y + op), m)
        return out

    def frame(self, level, t):
        S = self.S
        if self.cutout is not None:
            c = self.talk(self.cutout, level)
            k = 1.0 + 0.04 * level
            im = c.resize((int(c.width * k), int(c.height * (k + 0.02 * level))))
            bob = int(6 * math.sin(t * 2.2)) - int(10 * level)
            canvas = Image.new("RGBA", (500, 520), (0, 0, 0, 0))
            pos = ((500 - im.width) // 2, 500 - im.height + bob)
            glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            tint = Image.new("RGBA", im.size, (*ACC, 0))
            tint.putalpha(im.getchannel("A").point(lambda a: int(a * (0.35 + 0.5 * level))))
            glow.alpha_composite(tint, pos)
            canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(14 + 16 * level)))
            canvas.alpha_composite(im, pos)
            return canvas
        im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        ring = int(6 + 14 * level)
        d.ellipse((0, 0, S - 1, S - 1), fill=(*ACC, 255))
        d.ellipse((ring, ring, S - 1 - ring, S - 1 - ring), fill=(24, 28, 40, 255))
        if self.logo is not None:
            k = 1.0 + 0.05 * level
            lg = self.logo.resize((int(self.logo.width * k), int(self.logo.height * k)))
            m = Image.new("L", lg.size, 0)
            ImageDraw.Draw(m).ellipse((ring - 12, ring - 12, lg.width - ring + 12, lg.height - ring + 12), fill=255)
            im.paste(lg, ((S - lg.width) // 2, (S - lg.height) // 2), m)
        else:
            im.alpha_composite(shibass_dj(level, t, S), (0, 0))
        out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        out.paste(im, (0, 0), self.mask)
        glow = out.filter(ImageFilter.GaussianBlur(10 + 18 * level))
        canvas = Image.new("RGBA", (S + 60, S + 60), (0, 0, 0, 0))
        canvas.alpha_composite(glow, (30, 30))
        canvas.alpha_composite(out, (30, 30))
        return canvas



# ------------------------------------------------------------------ animated DAW screen
DAW_W, DAW_H = W - 180, 404
BPM = 140
KIND_WORDS = [("velocity", ("ולוסיטי", "עוצמה", "velocity", "humaniz")),
              ("pocket", ("קוונטייז", "כיס", "מוקדם", "מאוחר", "pocket", "quantiz")),
              ("kick", ("קיק", "kick")),
              ("length", ("אורך", "קצר", "ארוך", "length", "space")),
              ("notes", ("שורש", "קווינט", "אוקטב", "notes")),
              ("robot", ("רובוט", "robot"))]


def daw_kind(text, default="robot"):
    low = text.lower()
    for k, words in KIND_WORDS:
        if any(w in low for w in words):
            return k
    return default


def _pattern(kind, variant):
    """notes: (step, length, row, velocity, offset). 32 steps = 2 bars of 16ths. row 0..4 (4 = root)."""
    rnd = np.random.RandomState(7 + variant)
    notes, label = [], ""
    for beat in range(8):
        for sub in (1, 2, 3):
            st = beat * 4 + sub
            row, vel, off, ln = 4, 1.0, 0.0, 0.8
            if kind == "notes":
                row = [4, 4, 1, 4, 4, 0, 4, 2][beat] if sub == 3 else 4
            if kind in ("robot", "velocity"):
                if variant % 2:
                    vel = (1.0 if sub == 1 else 0.55) - 0.15 * rnd.rand()
                    off = (rnd.rand() - 0.5) * 0.25
            if kind == "pocket":
                off = [-0.3, 0.0, 0.3][variant % 3]
            if kind == "length":
                ln = 0.35 if variant % 2 == 0 else 1.0
            notes.append((st, ln, row, vel, off))
    label = {"robot": ["ROBOT  (100% grid, same velocity)", "HUMAN  (accents + feel)"],
             "velocity": ["FLAT VELOCITY", "ACCENTED + HUMANIZED"],
             "pocket": ["EARLY  (pushing)", "ON THE GRID", "LATE  (laid back)"],
             "kick": ["KICK + ROLLING BASS"],
             "length": ["SHORT NOTES  (tight)", "LONG NOTES  (full)"],
             "notes": ["ROOT  /  5TH  /  OCTAVE"]}[kind]
    return notes, label[variant % len(label)]


def daw_frame(kind, t):
    step_t = 60.0 / BPM / 4
    loop = 32 * step_t
    variant = int(t // loop)
    pos = (t % loop) / step_t  # playhead in steps
    notes, label = _pattern(kind, variant)
    im = Image.new("RGBA", (DAW_W, DAW_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, DAW_W - 1, DAW_H - 1), 22, fill=(16, 19, 27, 245), outline=(44, 50, 66), width=2)
    gx0, gx1 = 190, DAW_W - 24
    sw = (gx1 - gx0) / 32
    fs = font(26)
    d.text((24, 22), "SHIBASS  DAW", font=fs, fill=(110, 118, 140))
    d.text((DAW_W - 24, 22), label, font=fs, fill=ACC2, anchor="ra")
    lanes = {"KICK": (64, 114), "BASS": (130, 330), "VEL": (338, 396)}
    rows = ["OCT", "", "5TH", "", "ROOT"] if kind == "notes" else ["", "", "", "", ""]
    for name, (y0, y1) in lanes.items():
        d.rectangle((gx0, y0, gx1, y1), fill=(22, 26, 37))
        d.text((24, (y0 + y1) // 2), name, font=fs, fill=(150, 158, 178), anchor="lm")
    for i, r in enumerate(rows):
        if r:
            d.text((120, 130 + i * 40 + 20), r, font=font(20), fill=(120, 128, 150), anchor="lm")
    for k in range(33):
        x = gx0 + k * sw
        col = (70, 78, 100) if k % 4 == 0 else (34, 39, 54)
        if k % 16 == 0:
            col = (110, 118, 145)
        d.line((x, 64, x, 396), fill=col, width=2 if k % 4 == 0 else 1)
    # kick on every beat
    for beat in range(8):
        x = gx0 + beat * 4 * sw
        on = 0 <= pos - beat * 4 < 1.2
        d.rounded_rectangle((x + 3, 72, x + sw - 3, 106), 6, fill=(255, 255, 255) if on else ACC)
    for st, ln, row, vel, off in notes:
        x0 = gx0 + (st + off) * sw
        x1 = x0 + max(ln * sw - 4, 6)
        y0 = 130 + row * 40 + 4
        on = 0 <= pos - (st + off) < max(ln, 0.6)
        c = tuple(int(v * (0.45 + 0.55 * vel)) for v in ACC2)
        d.rounded_rectangle((x0 + 2, y0, x1, y0 + 32), 6, fill=(255, 255, 255) if on else c)
        h = int(52 * vel)
        d.rectangle((x0 + 4, 394 - h, x0 + 12, 394), fill=(255, 255, 255) if on else c)
    px = gx0 + pos * sw
    d.line((px, 56, px, 400), fill=(255, 93, 108), width=4)
    d.polygon([(px - 10, 50), (px + 10, 50), (px, 64)], fill=(255, 93, 108))
    return im

# ------------------------------------------------------------------ render
def screen_reader(ff, path, n_frames):
    p = subprocess.Popen([ff, "-v", "error", "-stream_loop", "0", "-i", str(path), "-vf",
                          f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0d0f14,fps={FPS}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    size = W * H * 3
    for _ in range(n_frames):
        b = p.stdout.read(size)
        if len(b) < size:
            break
        yield np.frombuffer(b, np.uint8).reshape(H, W, 3)
    p.kill()


def render(txt, wav, out, course="ShiBass", screen=None, logo=None, intro=4.0, daw=True):
    ff = ffmpeg_bin()
    title, paras = parse_lesson(txt)
    audio, sr = load_audio(wav, ff)
    dur = len(audio) / sr
    total = dur + intro
    n = int(total * FPS)
    level = np.concatenate([np.zeros(int(intro * FPS)), loudness_per_frame(audio, sr, n - int(intro * FPS))])
    slides, subs = build_timeline(paras, dur)
    slide_imgs = [np.asarray(make_slide(course, title, p, i + 1, len(slides), daw)) for i, (_, _, p) in enumerate(slides)]
    lesson_kind = daw_kind(Path(txt).stem.replace("-", " "))
    kinds = [daw_kind(p, lesson_kind) for _, _, p in slides]
    title_img = np.asarray(make_title_card(course, title))
    sub_imgs = [subtitle_layer(s) for _, _, s in subs]
    av = Avatar(logo)
    scr = screen_reader(ff, screen, n) if screen else None
    print(f"  {title}: {dur:.0f}s audio, {len(slides)} slides, {len(subs)} subtitles"
          f"{', screen: ' + Path(screen).name if screen else ''}")

    enc = subprocess.Popen([ff, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-itsoffset", str(intro), "-i", str(wav),
                            "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", str(out)],
                           stdin=subprocess.PIPE)
    si = 0
    t0 = time.time()
    for f in range(n):
        t = f / FPS
        ta = t - intro
        daw_k = None
        if ta < 0:
            base = title_img
        else:
            frame_scr = next(scr, None) if scr is not None else None
            if frame_scr is not None:
                base = frame_scr
            else:
                scr = None
                k = next((i for i, (a, b, _) in enumerate(slides) if a <= ta < b), len(slides) - 1)
                base = slide_imgs[k]
                daw_k = k
        img = Image.fromarray(base, "RGB").convert("RGBA")
        if daw and daw_k is not None:
            img.alpha_composite(daw_frame(kinds[daw_k], ta - slides[daw_k][0]), (90, 236))
            daw_k = None
        if ta >= 0:
            while si < len(subs) - 1 and ta >= subs[si][1]:
                si += 1
            img.alpha_composite(sub_imgs[si], (0, H - 300))
        if ta >= 0 or asset("portrait.png") is None:
            a = av.frame(float(level[f]) if f < len(level) else 0.0, t)
            img.alpha_composite(a, (40, H - a.height - 30))
        enc.stdin.write(img.convert("RGB").tobytes())
        if f % (FPS * 15) == 0 and f:
            done = f / n
            print(f"    {done * 100:4.0f}%  (~{(time.time() - t0) / done * (1 - done) / 60:.1f} min left)", flush=True)
    enc.stdin.close()
    enc.wait()
    print(f"  saved {out}")


def main():
    ap = argparse.ArgumentParser(description="Faceless lesson videos: slides/screen + talking ShiBass avatar.")
    ap.add_argument("lesson", nargs="?", help="lesson .txt")
    ap.add_argument("audio", nargs="?", help="lesson audio (wav/mp3)")
    ap.add_argument("--course", help="course folder with .txt lessons (audio in <folder>_audio)")
    ap.add_argument("--screen", help="screen recording for a single lesson")
    ap.add_argument("--avatar", default=r"C:\ShiBass_Voice\avatar.png", help="logo image (optional)")
    ap.add_argument("--name", default="ShiBass", help="course name shown on slides")
    ap.add_argument("--out", help="output mp4 (single lesson)")
    ap.add_argument("--redo", action="store_true")
    a = ap.parse_args()
    ASSETS["_dir"] = str(Path(a.avatar).parent) if a.avatar and a.avatar != "none" else "."

    if a.course:
        c = Path(a.course)
        aud, scr, vid = Path(str(c) + "_audio"), Path(str(c) + "_screen"), Path(str(c) + "_video")
        vid.mkdir(exist_ok=True)
        lessons = sorted(c.glob("*.txt"))
        print(f"Course '{c.name}': {len(lessons)} lessons -> {vid}")
        for i, t in enumerate(lessons, 1):
            w = next((aud / (t.stem + e) for e in (".wav", ".mp3") if (aud / (t.stem + e)).exists()), None)
            o = vid / (t.stem + ".mp4")
            if not w:
                print(f"[{i}/{len(lessons)}] {t.stem}: no audio yet in {aud} - skipped")
                continue
            if o.exists() and not a.redo:
                print(f"[{i}/{len(lessons)}] {t.stem}: video exists - skipped (--redo to rebuild)")
                continue
            s = next((scr / (t.stem + e) for e in (".mp4", ".mkv", ".mov") if (scr / (t.stem + e)).exists()), None)
            print(f"[{i}/{len(lessons)}] {t.stem}")
            render(t, w, o, a.name, s, a.avatar)
        print(f"\nDone. Videos: {vid}")
    elif a.lesson and a.audio:
        out = Path(a.out or Path(a.lesson).with_suffix(".mp4"))
        render(Path(a.lesson), Path(a.audio), out, a.name, a.screen, a.avatar)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
