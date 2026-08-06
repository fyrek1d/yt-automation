import os

from PIL import Image, ImageDraw, ImageFont


class ThumbnailGenerator:
    """Generates a YouTube thumbnail from the story title over parkour footage."""

    def __init__(self, output_dir: str, base_image: str = None):
        self.output_dir = output_dir
        self.base_image = base_image  # optional background png/jpg
        os.makedirs(output_dir, exist_ok=True)

    @staticmethod
    def _font(size: int, bold: bool = False) -> ImageFont:
        candidates = (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "C:/Windows/Fonts/arialbd.ttf",
            "arial.ttf",
        )
        for path in candidates:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size)
                except OSError:
                    continue
        return ImageFont.load_default()

    def create(self, story: dict, video_frame: str = None) -> str:
        bg = video_frame or self.base_image
        if not bg or not os.path.exists(bg):
            # Fallback: solid gradient background
            img = Image.new("RGB", (1280, 720), (24, 24, 28))
        else:
            img = Image.open(bg).convert("RGB")
            img = img.resize((1280, 720), Image.LANCZOS)

        draw = ImageDraw.Draw(img)

        # Darken background for contrast
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 140))
        img.paste(overlay, (0, 0), overlay)
        draw = ImageDraw.Draw(img)

        # Title text, wrapped
        title = story["title"][:80]
        words = title.split()
        lines = []
        while words:
            line = words.pop(0)
            while words and len(line) + 1 + len(words[0]) <= 28:
                line += " " + words.pop(0)
            lines.append(line)

        y = 300
        font = self._font(64, bold=True)
        for line in lines[:3]:
            bbox = draw.textbbox((0, 0), line, font=font)
            x = (1280 - (bbox[2] - bbox[0])) // 2
            draw.text((x + 3, y + 3), line, fill=(0, 0, 0), font=font)
            draw.text((x, y), line, fill=(255, 255, 255), font=font)
            y += 74

        out = os.path.join(self.output_dir, f"{story['id']}_thumb.jpg")
        img.save(out, "JPEG", quality=92)
        return out
