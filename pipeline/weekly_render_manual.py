"""手动出周日海报：读 weekly_collect --out 生成的 pack.json，渲染中英两张图。失败原因写成 ::error:: 注释，网页上能直接看到。"""
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "poster"))


def main(out="out"):
    try:
        import weekend_gen
        pack = json.loads((Path(out) / "pack.json").read_text(encoding="utf-8"))
        files = weekend_gen.render(pack, out)
        for name, path in files.items():
            print("rendered", name, path)
        return 0
    except Exception as exc:  # noqa: BLE001
        flat = " ".join((str(exc) + " | " + traceback.format_exc()[-600:]).split())[:900]
        print(f"::error title=手动出图失败::{flat}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:2]))
