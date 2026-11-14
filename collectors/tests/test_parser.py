# -*- coding: utf-8 -*-
"""解析器回归测试：python -m unittest tests.test_parser"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bounty import parser as parser_bounty
from _shared import regions


class TestAmount(unittest.TestCase):
    def test_cn_to_num(self):
        self.assertEqual(parser_bounty.cn_to_num("五万"), 50000)
        self.assertEqual(parser_bounty.cn_to_num("二十万"), 200000)
        self.assertEqual(parser_bounty.cn_to_num("一百五十万"), 1500000)
        self.assertEqual(parser_bounty.cn_to_num("叁万"), 30000)

    def test_to_yuan(self):
        self.assertEqual(parser_bounty.to_yuan("1.5", "万元"), 15000)
        self.assertEqual(parser_bounty.to_yuan("2", "亿元"), 200000000)
        self.assertEqual(parser_bounty.to_yuan("987,289", "元"), 987289)


class TestParse(unittest.TestCase):
    def _p(self, text, pub=None):
        return parser_bounty.parse_notice(text, pub)

    def test_ratio_and_cap(self):
        t = ("三、案件执行标的 181533.00元及利息 四、悬赏期限 自2026年10月30日至2027年10月29日止。"
             "五、悬赏内容及悬赏金额 1.申请人承诺，若举报人提供的财产线索经查证属实并执行到位，"
             "申请人按执行到位金额的10%支付悬赏金，最高不超过人民币10000元。"
             "2.对提供被执行人下落线索并协助法院实际控住被执行人的，支付悬赏金人民币1000元。"
             "举报电话:0432-64236460")
        r = self._p(t, "2026-10-30")
        self.assertEqual(r["subject_amount"], 181533.0)
        self.assertEqual(r["reward_ratio"], 0.10)
        self.assertEqual(r["reward_cap"], 10000.0)
        self.assertEqual(r["reward_est"], 10000.0)     # 181533*10% 被封顶压到 1 万
        self.assertEqual(r["valid_from"], "2026-10-30")
        self.assertEqual(r["valid_to"], "2027-10-29")
        self.assertEqual(r["contact_phone"], "0432-64236460")

    def test_year_not_taken_as_money(self):
        """'支付2023年9月11日' 不能被抽成 2023 元固定赏金。"""
        t = ("请求强制被执行人履行以下义务：一、支付借款1120000元及利息；"
             "二、支付2023年9月11日前的利息差额币26845.83元。"
             "悬赏金额及领取条件：凡向本院举报被执行人隐匿、转移财产线索后，"
             "经查找属实并实际执行到位的，申请人承诺给予举报人实际执行到位金额10%的资金"
             "（含税，最高不超过100000元）作为悬赏奖励。")
        r = self._p(t, "2026-04-27")
        self.assertEqual(r["subject_amount"], 1120000.0)
        self.assertEqual(r["reward_ratio"], 0.10)
        self.assertEqual(r["reward_cap"], 100000.0)
        self.assertIsNone(r["reward_fixed"])          # 关键：不能是 2023
        self.assertEqual(r["reward_est"], 100000.0)   # min(112000, 100000)

    def test_subject_not_taken_as_bounty(self):
        """'支付228万元' 是执行标的，不能当成固定赏金。"""
        t = ("申请人向本院申请强制执行，请求强制被申请人潘伟胜支付228万元及利息，本院已依法立案执行。"
             "悬赏金额及领取条件：凡向本院举报被执行人隐匿、转移财产线索后，经查找属实并实际执行到位的，"
             "申请人承诺给予举报人按实际执行到位金额的5%支付悬赏金。")
        r = self._p(t, "2026-05-19")
        self.assertEqual(r["subject_amount"], 2280000.0)
        self.assertEqual(r["reward_ratio"], 0.05)
        self.assertIsNone(r["reward_fixed"])
        self.assertEqual(r["reward_est"], 114000.0)

    def test_fixed_bounty_after_amount(self):
        """'支付500元悬赏金' 这种金额在前的固定赏金要抽到。"""
        t = ("执行标的：200000元。悬赏金额：提供被执行人财产线索并执行到位的，"
             "按执行到位金额的5%支付悬赏金；提供被执行人并执行到位的，支付500元悬赏金。")
        r = self._p(t, "2026-11-04")
        self.assertEqual(r["reward_fixed"], 500.0)

    def test_tiered_rate(self):
        t = ("执行标的：人民币766260764.82元。具体比例如下：基数金额100万元以下的部分 费率18%；"
             "100-500万元的部分 费率15%。但悬赏总额不超过500万元。")
        r = self._p(t)
        self.assertEqual(r["reward_ratio"], 0.18)
        self.assertEqual(r["reward_cap"], 5000000.0)
        self.assertEqual(r["reward_est"], 5000000.0)

    def test_reward_type(self):
        self.assertEqual(self._p("提供被执行人下落线索的")["reward_type"], "whereabouts")
        self.assertEqual(self._p("提供被执行人财产线索的")["reward_type"], "property")
        self.assertEqual(self._p("提供财产线索或下落线索的")["reward_type"], "both")

    def test_bounty_signal(self):
        self.assertTrue(parser_bounty.looks_like_bounty("执行悬赏公告 被执行人张三"))
        self.assertFalse(parser_bounty.looks_like_bounty("本院定于2026年1月1日开庭审理"))
        self.assertFalse(parser_bounty.looks_like_bounty("悬赏广告位招租"))

    def test_debtor(self):
        """法院官网源没有结构化被执行人字段，只能从正文抽。"""
        cases = [
            ("被执行人：董德波，男，1983年2月18日出生", "董德波"),
            ("被执行人周赵，男，汉族，1990年7月28日出生", "周赵"),
            ("被执行人：贵州金岛湖酒店管理有限公司，住所地贵州省…", "贵州金岛湖酒店管理有限公司"),
            ("被执行人董德波借款合同纠纷一案", "董德波"),
            # 顿号并列名单只取第一个，不能把"吴流明"截成"吴流"
            ("被执行人吴流明、易连秀民间借贷纠纷一案", "吴流明"),
            # 名字后紧跟证件说明
            ("被执行人：刘庆明身份证号：4425261965****4015", "刘庆明"),
        ]
        for text, want in cases:
            self.assertEqual(parser_bounty.extract_debtor(text), want, text)

    def test_debtor_not_polution(self):
        """泛指表述不能被当成被执行人名字。"""
        for text in [
            "现申请执行人向本院申请公告查找被执行人财产线索",
            "被执行人下落不明，现公告查找",
            "被执行人至今未履行生效法律文书确定的付款义务",
            "被执行人已经履行全部义务，本悬赏公告自动失效",
        ]:
            self.assertIsNone(parser_bounty.extract_debtor(text), text)

    def test_fixed_bounty_not_from_rate_table(self):
        """分段费率档位里的金额不是固定赏金，'以X元为限'是封顶不是赏金。"""
        t = ("将根据申请执行人的书面承诺，按照实际执行到位款项的一定比例发放悬赏金："
             "1.执行到位款项5000万元(含本数)以内的，按5%发放悬赏金")
        r = self._p(t)
        self.assertIsNone(r["reward_fixed"])
        self.assertAlmostEqual(r["reward_ratio"], 0.05)

        t2 = "给予举报人实际执行到位金额1%的资金（含税，以215737.51元为限）"
        r2 = self._p(t2)
        self.assertIsNone(r2["reward_fixed"])


class TestRegions(unittest.TestCase):
    def test_resolve(self):
        cases = {
            "深圳市龙岗区人民法院": ("广东省", "深圳"),
            "成都市温江区人民法院": ("四川省", "成都"),
            "北京市朝阳区人民法院": ("北京市", "北京市"),   # 不能被辽宁朝阳市抢走
            "天津市第二中级人民法院": ("天津市", "天津市"),
            "新疆维吾尔自治区乌鲁木齐市中级人民法院": ("新疆维吾尔自治区", "乌鲁木齐"),
            "定远县人民法院": ("安徽省", None),
        }
        for name, (p, c) in cases.items():
            got_p, got_c, conf = regions.resolve(name)
            self.assertEqual(got_p, p, name)
            self.assertGreater(conf, 0, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
