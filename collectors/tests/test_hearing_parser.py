# -*- coding: utf-8 -*-
"""开庭公告解析回归测试：python tests/test_hearing_parser.py"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "hearing")):
    if p not in sys.path:
        sys.path.insert(0, p)

from hearing import parser as ph
from hearing import court_schedule


class TestCause(unittest.TestCase):
    def test_plain(self):
        t = "本院受理原告中国农业银行股份有限公司新化县支行诉你金融借款合同纠纷一案"
        self.assertEqual(ph.extract_cause(t), "金融借款合同纠纷")

    def test_not_polluted_by_party_name(self):
        """「被告赵金龙租赁合同纠纷」不能把赵金龙吞进案由。"""
        t = "上诉人牛保帅与赵金龙租赁合同纠纷一案，本院定于2026年12月14日开庭。"
        self.assertEqual(ph.extract_cause(t), "租赁合同纠纷")

    def test_longest_suffix(self):
        t = "原告李芝树诉德阳蜗牛科技有限公司房屋租赁合同纠纷一案"
        self.assertEqual(ph.extract_cause(t), "房屋租赁合同纠纷")

    def test_company_prefix(self):
        t = "原告王昌横与昆山美吉特置业有限公司金融借款合同纠纷一案"
        self.assertEqual(ph.extract_cause(t), "金融借款合同纠纷")

    def test_group(self):
        cases = {
            "建设工程施工合同纠纷": "建筑工程",
            "装饰装修合同纠纷": "建筑工程",
            "票据追索权纠纷": "票据",
            "侵害商标权纠纷": "商标知产",
            "金融借款合同纠纷": "金融借贷",
            "股权转让纠纷": "公司股权",
            "买卖合同纠纷": "合同纠纷",
        }
        for cause, group in cases.items():
            self.assertEqual(ph.cause_group(cause), group, cause)

    def test_group_none_for_non_commercial(self):
        self.assertIsNone(ph.cause_group("申请破产清算"))


class TestCaseNo(unittest.TestCase):
    def test_full_width(self):
        no, kind = ph.extract_case_no("（2026）粤0303民初27098号")
        self.assertEqual(no, "（2026）粤0303民初27098号")
        self.assertEqual(kind, "民初")

    def test_half_width(self):
        no, _ = ph.extract_case_no("(2026)粤01民终28947号")
        self.assertEqual(no, "（2026）粤01民终28947号")

    def test_missing(self):
        self.assertEqual(ph.extract_case_no("没有案号的公告")[0], None)


class TestHearingTime(unittest.TestCase):
    def test_anchor(self):
        t = "并定于2027年1月14日14时30分在本院第十三审判庭开庭审理"
        dt, est = ph.extract_hearing_time(t, "2026-11-11")
        self.assertEqual(dt.strftime("%Y-%m-%d %H:%M"), "2027-01-14 14:30")
        self.assertFalse(est)

    def test_afternoon(self):
        t = "本案于2026年12月28日下午4时30分在本院第十八审判庭公开开庭审理"
        dt, _ = ph.extract_hearing_time(t, "2026-11-11")
        self.assertEqual(dt.strftime("%H:%M"), "16:30")

    def test_numeric(self):
        t = "本院决定于2027年01月06日09:30-10:30在第37法庭开庭"
        dt, _ = ph.extract_hearing_time(t, "2026-10-22")
        self.assertEqual(dt.strftime("%Y-%m-%d %H:%M"), "2027-01-06 09:30")

    def test_reject_before_publish(self):
        """开庭日早于公告日一定不是本案开庭时间。"""
        t = "双方于2026年3月1日签订合同，本院定于2026年3月10日开庭。"
        dt, _ = ph.extract_hearing_time(t, "2026-11-11")
        self.assertIsNone(dt)

    def test_estimated(self):
        t = ("自公告之日起经过30日即视为送达。提出答辩状和举证的期限为公告期满后的"
             "15日内，并定于举证期满后的第三天上午8时30分在本院第五审判庭审理。")
        dt, est = ph.extract_hearing_time(t, "2026-11-11")
        self.assertTrue(est)
        self.assertEqual(dt.strftime("%Y-%m-%d"), "2026-12-29")


class TestParties(unittest.TestCase):
    def test_company(self):
        self.assertTrue(ph.is_company("海南翼晟劳务服务有限公司"))
        self.assertTrue(ph.is_company("淮安市翔云米业有限公司"))
        self.assertFalse(ph.is_company("田进虎"))
        self.assertFalse(ph.is_company("张先生"))

    def test_defendant_not_polluted(self):
        t = ("原告杨桂友与被告江苏扬建集团有限公司、魏大胜、金湖县国控城市建设"
             "发展集团有限公司建设工程施工合同纠纷一案")
        _, defendant, dtype, comp, _ = ph.extract_parties(t)
        self.assertNotIn("纠纷", defendant)
        self.assertEqual(dtype, "mixed")
        self.assertEqual(comp, 2)

    def test_send_to_fallback(self):
        t = "本院受理的上诉人仲波与被上诉人谈步金建设工程施工合同纠纷一案"
        _, defendant, dtype, _, _ = ph.extract_parties(
            t, send_to="谈步金;海南翼晟劳务服务有限公司")
        self.assertEqual(dtype, "mixed")
        self.assertIn("海南翼晟", defendant)

    def test_single_person(self):
        _, _, dtype, _, pers = ph.extract_parties("原告庞燕华与被告高畅买卖合同纠纷一案")
        self.assertEqual(dtype, "person")
        self.assertEqual(pers, 1)


class TestAmount(unittest.TestCase):
    def test_claim(self):
        t = ("原告诉讼请求为：1.判决被告支付货款1,409,609.5元；"
             "2.判决被告支付违约金100000元。")
        amt, basis = ph.extract_amount(t)
        self.assertEqual(amt, 1409609.5)
        self.assertEqual(basis, "诉讼请求")

    def test_skip_fee(self):
        t = "原告请求被告偿还本金130000元，并承担本案诉讼费5250元。"
        amt, _ = ph.extract_amount(t)
        self.assertEqual(amt, 130000)

    def test_none(self):
        self.assertEqual(ph.extract_amount("现依法向你公告送达开庭传票。")[0], None)


class TestLooksLikeHearing(unittest.TestCase):
    def test_judgment_excluded(self):
        t = ("原告某银行与你金融借款合同纠纷一案，已审理终结。现依法向你公告送达"
             "本院民事判决书，判决如下：被告偿还本金169000元。")
        self.assertFalse(ph.looks_like_hearing(t))

    def test_hearing_kept(self):
        t = ("本院受理原告某银行诉你金融借款合同纠纷一案，现依法向你公告送达开庭传票，"
             "并定于2027年1月14日14时30分在本院第十三审判庭开庭审理。")
        self.assertTrue(ph.looks_like_hearing(t))


class TestScheduleTable(unittest.TestCase):
    HTML = """
    <table class="pageTable">
      <tr><td>序号</td><td>审理法院</td><td>开庭时间</td><td>法庭</td>
          <td>案号</td><td>案由</td></tr>
      <tr height="32"><td align="center">1</td>
          <td align="center">广东省广州市中级人民</td>
          <td align="center">20261111 09:15 </td>
          <td align="center">第46法庭（二楼西区）</td>
          <td align="center">（2026）粤01民终11980号</td>
          <td align="center">损害公司债权人利益责任纠纷</td></tr>
      <tr height="32"><td align="center">2</td>
          <td align="center">广东省广州市中级人民</td>
          <td align="center">20261111 09:30 </td>
          <td align="center">第52法庭（二楼西区）</td>
          <td align="center">（2026）粤01民终27169号</td>
          <td align="center">建设工程合同纠纷</td></tr>
    </table>
    """

    def test_parse(self):
        site = {"court": "广东省广州市中级人民法院",
                "url": "https://example.invalid/x.html",
                "province": "广东省", "city": "广州市"}
        rows = court_schedule.parse_table(self.HTML, site)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["hearing_at"], "2026-11-11 09:15")
        self.assertEqual(rows[0]["cause"], "损害公司债权人利益责任纠纷")
        self.assertEqual(rows[1]["courtroom"], "第52法庭（二楼西区）")
        self.assertEqual(rows[0]["court"], "广东省广州市中级人民法院")


if __name__ == "__main__":
    unittest.main(verbosity=2)
