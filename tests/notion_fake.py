"""In-memory stand-in for the Notion blocks API, laid out like the real ledger page."""
import copy
import itertools


def rt(text):
    return [{"type": "text", "text": {"content": text}, "plain_text": text}] if text else []


class FakeNotion:
    PAGE = "3cfb72fc8cae808d9deecfdc21f4cba5"

    def __init__(self):
        self.blocks, self.kids, self.counter = {}, {}, itertools.count(1)
        self.writes = []
        self.fail_on = None  # callable(method, path, body) -> bool, raises once when True
        self.kids[self.PAGE] = []
        summary = [["项目", "数值"], ["已了结笔数", "17 笔"], ["持仓中笔数", "1 笔"], ["挂单未成交", "1 笔"], ["公开作废", "10 笔"],
                   ["过期未成交", "6 笔"], ["累计盈亏", "+683.72 美元"], ["账户收益率", "+6.84%"],
                   ["当前账户资金", "10,683.72 美元"], ["更新至", "第 35 期 · 2026-10-16（新增ETH做空限价挂单1张）；上期记录：第 34 期 · 2026-10-15（无新单）"]]
        head = ["期号", "发布日", "标的", "档位", "方向", "仓位", "进场", "止损（收盘）", "目标", "状态", "成交日", "平仓价", "币价盈亏", "账户盈亏", "备注"]
        active = [head, ["35", "2026-10-16", "ETH", "慢档日线", "做空", "0.12", "2585.57", "2637.28", "2378.72", "挂单中", "—", "—", "—", "—", "原备注"],
                  ["36", "2026-10-19", "SOL", "慢档日线", "做空", "0.33", "150", "155", "130", "持仓中", "2026-10-19", "—", "—", "—", "持仓"]]
        ended = [head, ["32", "2026-10-13", "DOGE", "慢档日线", "做空", "0.17", "0.088303", "0.090873", "0.078025", "公开作废", "—", "—", "—", "—", "撤单"],
                 ["17", "2026-09-14", "ETH", "慢档日线", "做多", "0.33", "2389.12", "2662.01", "2575.10", "已了结", "2026-09-15", "2572.88", "+7.69%", "+50.8 美元", "n"],
                 ["9", "2026-08-31", "XRP", "慢档日线", "做多", "1.00", "1.3023", "1.4513", "1.4680", "已了结", "2026-09-15", "1.4204", "+9.07%", "+181.4 美元", "n"],
                 ["16", "2026-09-11", "SOL", "慢档日线", "做多", "0.33", "97.83", "119.78", "107.07", "已了结", "2026-09-15", "118.82", "+21.46%", "+141.6 美元", "n"],
                 ["17", "2026-09-14", "XRP", "慢档日线", "做多", "0.33", "1.3668", "1.3312", "1.5093", "已了结", "2026-09-14", "1.2826", "-6.16%", "-40.7 美元", "n"],
                 ["15", "2026-09-10", "XRP", "慢档日线", "做多", "0.33", "1.3959", "1.3597", "1.5408", "已了结", "2026-09-11", "1.3557", "-2.88%", "-19.0 美元", "n"],
                 ["15", "2026-09-10", "BNB", "慢档日线", "做多", "0.33", "728.93", "714.35", "787.24", "已了结", "2026-09-11", "712.54", "-2.25%", "-14.8 美元", "n"]]
        registry = [["期号", "标的", "条件", "若触发则挂", "是否触发", "结论"],
                    ["35", "ETH", "成交前ETH资金费率年化转负", "撤单（空头也拥挤）", "否", "每期复核"]]
        self.add_page("heading_2", {"rich_text": rt("一、汇总")})
        self.add_table(summary)
        self.add_page("heading_2", {"rich_text": rt("二、当前在场")})
        self.add_table(active)
        self.add_page("heading_2", {"rich_text": rt("三、已结束")})
        self.add_table(ended)
        self.add_page("heading_2", {"rich_text": rt("四、条件单登记簿")})
        self.add_table(registry)
        self.add_page("heading_2", {"rich_text": rt("五、状态说明")})
        self.add_page("paragraph", {"rich_text": rt("已了结：已平仓。")})
        self.add_page("paragraph", {"rich_text": rt("链上大叔 ｜ 流动性到 Alpha ・ @Uncle_Onchain")})

    def new_id(self):
        return f"b{next(self.counter):031d}"

    def add_page(self, kind, data, index=None):
        bid = self.new_id()
        self.blocks[bid] = {"id": bid, "type": kind, kind: data}
        if kind == "table":
            self.kids[bid] = []
        position = len(self.kids[self.PAGE]) if index is None else index
        self.kids[self.PAGE].insert(position, bid)
        return bid

    def add_table(self, rows):
        bid = self.add_page("table", {"table_width": len(rows[0]), "has_column_header": True, "has_row_header": False})
        for row in rows:
            self.add_row(bid, row)
        return bid

    def add_row(self, table_id, cells):
        bid = self.new_id()
        self.blocks[bid] = {"id": bid, "type": "table_row", "table_row": {"cells": [rt(c) for c in cells]}}
        self.kids[table_id].append(bid)

    def texts(self, table_id):
        return [[c[0]["text"]["content"] if c else "" for c in self.blocks[b]["table_row"]["cells"]] for b in self.kids[table_id]]

    def table_by_title(self, prefix):
        order = self.kids[self.PAGE]
        for i, bid in enumerate(order):
            block = self.blocks[bid]
            if block["type"].startswith("heading") and block[block["type"]]["rich_text"] and \
                    block[block["type"]]["rich_text"][0]["text"]["content"].startswith(prefix):
                for later in order[i + 1:]:
                    if self.blocks[later]["type"] == "table":
                        return later
        return None

    def _public(self, bid):
        block = copy.deepcopy(self.blocks[bid])
        data = block[block["type"]]
        lists = data.get("cells", []) if block["type"] == "table_row" else [data.get("rich_text", [])]
        for rich_list in lists:  # Notion fills plain_text on read
            for part in rich_list:
                part.setdefault("plain_text", part["text"]["content"])
        return block

    def call(self, method, path, body=None):
        if self.fail_on and method != "GET" and self.fail_on(method, path, body):
            self.fail_on = None
            raise RuntimeError("Notion HTTP 500 注入的故障")
        if method == "GET":
            bid = path.split("/blocks/")[1].split("/children")[0]
            return {"results": [self._public(b) for b in self.kids[bid]], "has_more": False}
        if method == "PATCH" and path.endswith("/children"):
            self.writes.append((method, path, copy.deepcopy(body)))
            bid = path.split("/blocks/")[1].split("/children")[0]
            index = None
            if body.get("after"):
                index = self.kids[bid].index(body["after"]) + 1
            for child in body["children"]:
                if bid == self.PAGE:
                    kind = child["type"]
                    data = copy.deepcopy(child[kind])
                    rows = data.pop("children", None)
                    new = self.add_page(kind, data, index)
                    index = None if index is None else index + 1
                    for row in rows or []:
                        self.add_row(new, [c[0]["text"]["content"] if c else "" for c in row["table_row"]["cells"]])
                else:
                    cells = child["table_row"]["cells"]
                    width = self.blocks[bid]["table"]["table_width"]
                    assert len(cells) == width, "列数不符"
                    self.add_row(bid, [c[0]["text"]["content"] if c else "" for c in cells])
            return {"results": []}
        if method == "PATCH":
            self.writes.append((method, path, copy.deepcopy(body)))
            bid = path.split("/blocks/")[1]
            self.blocks[bid]["table_row"] = copy.deepcopy(body["table_row"])
            return self.blocks[bid]
        raise AssertionError((method, path))
