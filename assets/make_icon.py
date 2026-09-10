"""產生程式圖示 assets/app.ico（米灰擬物底 + 青綠下載箭頭）。只在打包前需要執行一次。"""
from PIL import Image, ImageDraw, ImageFilter

S = 512
img = Image.new('RGBA', (S, S), (0, 0, 0, 0))

# 底部陰影（右下深、左上亮的柔影感）
shadow = Image.new('RGBA', (S, S), (0, 0, 0, 0))
ImageDraw.Draw(shadow).rounded_rectangle((70, 70, S - 40, S - 40), radius=120, fill=(120, 110, 95, 150))
shadow = shadow.filter(ImageFilter.GaussianBlur(26))
img.alpha_composite(shadow)
light = Image.new('RGBA', (S, S), (0, 0, 0, 0))
ImageDraw.Draw(light).rounded_rectangle((40, 40, S - 70, S - 70), radius=120, fill=(255, 255, 255, 200))
light = light.filter(ImageFilter.GaussianBlur(26))
img.alpha_composite(light)

# 米灰圓角底板
tile = Image.new('RGBA', (S, S), (0, 0, 0, 0))
ImageDraw.Draw(tile).rounded_rectangle((56, 56, S - 56, S - 56), radius=118, fill=(231, 228, 222, 255))
img.alpha_composite(tile)

# 凹陷圓盤
inner = Image.new('RGBA', (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(inner)
d.ellipse((128, 128, S - 128, S - 128), fill=(222, 218, 211, 255))
img.alpha_composite(inner)

# 青綠箭頭 + 托盤
d = ImageDraw.Draw(img)
teal = (61, 184, 180, 255)
cx = S // 2
d.rounded_rectangle((cx - 24, 176, cx + 24, 296), radius=18, fill=teal)          # 箭桿
d.polygon([(cx - 78, 268), (cx + 78, 268), (cx, 350)], fill=teal)                # 箭頭
d.rounded_rectangle((cx - 96, 372, cx + 96, 402), radius=15, fill=teal)          # 托盤

sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
img.save('assets/app.ico', format='ICO', sizes=sizes)
img.resize((256, 256), Image.LANCZOS).save('assets/app.png')
print('icon written')
