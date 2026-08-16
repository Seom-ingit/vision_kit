"""geometry 几何自洽校验的单元测试（unittest，无第三方依赖）。

运行：
    python test_geometry.py                  # 直接运行（脚本模式）
    python -m unittest vision_kit.test_geometry
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vision_kit.geometry import overall_consistent, run_geometry_checks  # noqa: E402


class GeometryChecksTest(unittest.TestCase):
    def test_triangle_sum_ok(self):
        checks = run_geometry_checks({"vectors": {"A": [70], "B": [60], "C": [50]}})
        self.assertTrue(overall_consistent(checks))

    def test_triangle_sum_bad(self):
        checks = run_geometry_checks({"vectors": {"A": [70], "B": [60], "C": [40]}})
        bad = [c for c in checks if c["rule"] == "triangle_sum" and not c["passed"]]
        self.assertTrue(bad)
        self.assertFalse(overall_consistent(checks))

    def test_vector_sum_correct(self):
        checks = run_geometry_checks(
            {"vectors": {"a1": [1, 1, 2, 2], "a2": [1, 2, 1, 3], "b": [2, 3, 3, 5]}}
        )
        ok = [c for c in checks if c["rule"] == "vector_sum" and c["passed"]]
        self.assertTrue(ok)
        self.assertTrue(overall_consistent(checks))

    def test_vector_sum_near_miss(self):
        # a1+a2 本应 = b 但 b 差一点 → 判"接近不等"
        checks = run_geometry_checks(
            {"vectors": {"a1": [1, 1, 2, 2], "a2": [1, 2, 1, 3], "b": [2, 3, 2, 6]}}
        )
        bad = [c for c in checks if c["rule"] == "vector_sum" and not c["passed"]]
        self.assertTrue(bad)
        self.assertFalse(overall_consistent(checks))

    def test_unrelated_vectors_no_false_positive(self):
        checks = run_geometry_checks(
            {"vectors": {"p": [1, 0, 0], "q": [0, 1, 0], "r": [0, 0, 1]}}
        )
        # 互不相关的单位向量不应被当成矛盾
        bad = [c for c in checks if not c["passed"]]
        self.assertEqual(bad, [])

    def test_matrix_multiplicity_ok(self):
        # 2x2 · 2x3 可乘 → 应给出正面确认，且不因其不可交换而判负
        checks = run_geometry_checks(
            {"matrices": {"A": [[1, 2], [3, 4]], "B": [[1, 2, 3], [4, 5, 6]]}}
        )
        ok = [c for c in checks if c["rule"] == "matrix_multiplicity" and c["passed"]]
        self.assertTrue(ok)
        bad = [c for c in checks if c["rule"] == "matrix_multiplicity" and not c["passed"]]
        self.assertEqual(bad, [])
        self.assertTrue(overall_consistent(checks))

    def test_negative_length(self):
        checks = run_geometry_checks({"vectors": {"AB": [-3, 4]}})
        bad = [c for c in checks if c["rule"] == "plausible_range"]
        self.assertTrue(bad)
        self.assertFalse(overall_consistent(checks))

    def test_negative_coordinate_not_flagged(self):
        # 坐标向量含负值（如点 P=(-2,0)）不应被误报为"负长度"
        checks = run_geometry_checks({"vectors": {"P": [-2, 0, 3]}})
        self.assertTrue(overall_consistent(checks))


if __name__ == "__main__":
    unittest.main()
