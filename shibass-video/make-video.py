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


def make_slide(course, title, para, idx, total):
    img = gradient_bg()
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((90, 70, W - 90, 150), 22, fill=(21, 25, 34))
    f_small, f_title, f_body = font(34), font(64), font(54)
    d.text((W - 130, 110), rtl(f"{course}  ·  {title}"), font=f_small, fill=(152, 161, 179), anchor="rm")
    d.text((130, 110), f"{idx}/{total}", font=f_small, fill=(152, 161, 179), anchor="lm")
    first = re.split(r"(?<=[.!?:])\s+", para)[0]
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


def make_title_card(course, title):
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
                im.thumbnail((440, 440))
                self.cutout = im
            else:
                self.logo = ImageOps.fit(im, (self.S - 24, self.S - 24))
        self.mask = Image.new("L", (self.S, self.S), 0)
        ImageDraw.Draw(self.mask).ellipse((0, 0, self.S - 1, self.S - 1), fill=255)

    def frame(self, level, t):
        S = self.S
        if self.cutout is not None:
            c = self.cutout
            k = 1.0 + 0.06 * level
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
            cx, cy = S // 2, S // 2 + 6
            bob = int(4 * math.sin(t * 2.2))
            d.ellipse((cx - 120, cy - 128 + bob, cx + 120, cy + 112 + bob), fill=(46, 52, 72))
            d.arc((cx - 132, cy - 150 + bob, cx + 132, cy + 40 + bob), 200, 340, fill=ACC2, width=14)  # headphones
            d.rounded_rectangle((cx - 150, cy - 40 + bob, cx - 112, cy + 40 + bob), 12, fill=ACC2)
            d.rounded_rectangle((cx + 112, cy - 40 + bob, cx + 150, cy + 40 + bob), 12, fill=ACC2)
            blink = (t % 4.1) < 0.12
            for ex in (-46, 46):
                if blink:
                    d.line((cx + ex - 16, cy - 30 + bob, cx + ex + 16, cy - 30 + bob), fill=(235, 238, 245), width=6)
                else:
                    d.ellipse((cx + ex - 16, cy - 46 + bob, cx + ex + 16, cy - 14 + bob), fill=(235, 238, 245))
                    d.ellipse((cx + ex - 6, cy - 36 + bob, cx + ex + 6, cy - 24 + bob), fill=(20, 22, 30))
            mh = 6 + int(46 * level)
            d.rounded_rectangle((cx - 44, cy + 34 + bob, cx + 44, cy + 34 + mh + bob), min(22, mh // 2 + 3),
                                fill=(20, 22, 30))
            if mh > 20:
                d.rounded_rectangle((cx - 28, cy + 34 + mh - 12 + bob, cx + 28, cy + 34 + mh + bob), 8,
                                    fill=(255, 93, 108))
        out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        out.paste(im, (0, 0), self.mask)
        glow = out.filter(ImageFilter.GaussianBlur(10 + 18 * level))
        canvas = Image.new("RGBA", (S + 60, S + 60), (0, 0, 0, 0))
        canvas.alpha_composite(glow, (30, 30))
        canvas.alpha_composite(out, (30, 30))
        return canvas


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


def render(txt, wav, out, course="ShiBass", screen=None, logo=None, intro=4.0):
    ff = ffmpeg_bin()
    title, paras = parse_lesson(txt)
    audio, sr = load_audio(wav, ff)
    dur = len(audio) / sr
    total = dur + intro
    n = int(total * FPS)
    level = np.concatenate([np.zeros(int(intro * FPS)), loudness_per_frame(audio, sr, n - int(intro * FPS))])
    slides, subs = build_timeline(paras, dur)
    slide_imgs = [np.asarray(make_slide(course, title, p, i + 1, len(slides))) for i, (_, _, p) in enumerate(slides)]
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
        img = Image.fromarray(base, "RGB").convert("RGBA")
        if ta >= 0:
            while si < len(subs) - 1 and ta >= subs[si][1]:
                si += 1
            img.alpha_composite(sub_imgs[si], (0, H - 300))
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
