# -*- coding: utf-8 -*-
"""法院名归一回归测试：python tests/test_prd.py

深圳和珠三角共用一套 detect_court / canon_court，改动时最容易出两类错：
  - 把东莞/惠州/广州的法院名补成「深圳市…」前缀
  - 正文里「上诉至广州市中级人民法院」被识别成深圳中院
这里把这两种情况钉死。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from _shared import courts as sz


class TestCanon(unittest.TestCase):
    def test_shenzhen_district_unchanged(self):
        self.assertEqual(sz.canon_court("龙岗区人民法院"), "深圳市龙岗区人民法院")
        self.assertEqual(sz.canon_court("深圳市福田区人民法院"), "深圳市福田区人民法院")

    def test_prd_full_names_untouched(self):
        for name in ("东莞市第一人民法院", "惠州市惠城区人民法院",
                     "博罗县人民法院", "广州互联网法院"):
            self.assertEqual(sz.canon_court(name), name)

    def test_nansha_alias(self):
        self.assertEqual(
            sz.canon_court("广东自由贸易区南沙片区人民法院"),
            "广州市南沙区人民法院")

    def test_intermediate_court_not_rewritten(self):
        self.assertEqual(sz.canon_court("广州市中级人民法院"),
                         "广东省广州市中级人民法院")
        self.assertEqual(sz.canon_court("惠州市中级人民法院"),
                         "惠州市中级人民法院")


class TestClean(unittest.TestCase):
    def test_no_shenzhen_prefix_on_prd(self):
        self.assertEqual(sz._clean_court("东莞市第二人民法院"), "东莞市第二人民法院")
        self.assertEqual(sz._clean_court("博罗县人民法院"), "博罗县人民法院")

    def test_bare_district_still_prefixed(self):
        self.assertEqual(sz._clean_court("龙华区人民法院"), "深圳市龙华区人民法院")


class TestDetect(unittest.TestCase):
    def test_body_hit(self):
        body = "本院定于二〇二六年十二月一日开庭。惠州市惠阳区人民法院"
        self.assertEqual(sz.detect_court("", body, "惠州市惠城区人民法院"),
                         "惠州市惠阳区人民法院")

    def test_default_kept_when_unknown(self):
        self.assertEqual(sz.detect_court("", "本院定于明日开庭。", "东莞市第三人民法院"),
                         "东莞市第三人民法院")


if __name__ == "__main__":
    unittest.main(verbosity=2)
