"""Fake e-paper panel shared by the hardware and sleep-screen tests."""
from PIL import Image


class FakeEPD:
    width, height = 400, 300

    def __init__(self):
        self.calls = []

    def init(self): self.calls.append("init")
    def display(self, buf): self.calls.append(("display", len(buf)))
    def display_Partial(self, buf): self.calls.append(("partial", len(buf)))
    def sleep(self): self.calls.append("sleep")


class FakeDriver:
    def __init__(self): self.epd = FakeEPD()
    def EPD(self): return self.epd


def image(black_at=None):
    img = Image.new("1", (400, 300), 1)
    if black_at:
        img.putpixel(black_at, 0)
    return img
