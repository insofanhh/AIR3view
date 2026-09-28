"""Create the install and shortcut icon from the same simple tray mark."""

from pathlib import Path
import sys

from PIL import Image, ImageDraw


def main(path):
    image = Image.new('RGBA', (256, 256), '#171b23')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((11, 11, 244, 244), radius=52, fill='#b9f46a')
    draw.polygon(((89, 59), (198, 128), (89, 197)), fill='#171b23')
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format='ICO', sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])


if __name__ == '__main__':
    main(sys.argv[1])
