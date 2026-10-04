import itertools
import unittest
from unittest.mock import patch

import numpy as np

import crop


class CropGeometryTests(unittest.TestCase):
    def test_rectangle_points_keep_top_left_clockwise_order(self) -> None:
        points = np.array([[90, 70], [10, 10], [10, 70], [90, 10]], dtype=np.float32)

        ordered = crop.order_quad_points(points)

        np.testing.assert_array_equal(ordered, [[10, 10], [90, 10], [90, 70], [10, 70]])

    def test_rectangle_extraction_preserves_distinguishable_corners(self) -> None:
        image = np.zeros((80, 100, 3), dtype=np.uint8)
        colors = [(0, 0, 255), (0, 255, 0), (255, 255, 0), (255, 0, 0)]
        image[0:10, 0:10] = colors[0]
        image[0:10, 90:100] = colors[1]
        image[70:80, 90:100] = colors[2]
        image[70:80, 0:10] = colors[3]
        points = [[99, 79], [0, 0], [0, 79], [99, 0]]

        with patch("crop.detect_upright_rotation", return_value=0):
            extracted = crop.extract_photo(image, points)

        self.assertIsNotNone(extracted)
        assert extracted is not None
        self.assertEqual(extracted.shape[:2], (79, 99))
        observed = [extracted[y, x] for y, x in [(2, 2), (2, -3), (-3, -3), (-3, 2)]]
        for pixel, color in zip(observed, colors):
            np.testing.assert_array_equal(pixel, color)

    def test_diamond_orders_four_distinct_cyclic_corners(self) -> None:
        points = np.array([[50, 0], [100, 50], [50, 100], [0, 50]], dtype=np.float32)

        ordered = crop.order_quad_points(points)

        np.testing.assert_array_equal(ordered, points)

    def test_convex_order_is_stable_for_every_input_permutation(self) -> None:
        for corners in (
            [[10, 10], [90, 10], [90, 70], [10, 70]],
            [[50, 0], [100, 50], [50, 100], [0, 50]],
            [[20, 15], [140, 35], [120, 120], [10, 100]],
        ):
            for permutation in itertools.permutations(corners):
                with self.subTest(corners=corners, permutation=permutation):
                    ordered = crop.order_quad_points(np.array(permutation, dtype=np.float64))
                    self.assertEqual(ordered.dtype, np.float32)
                    np.testing.assert_array_equal(ordered, corners)

    def test_degenerate_selections_return_none_before_perspective_processing(self) -> None:
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        selections = {
            "duplicate": [[0, 0], [10, 0], [10, 10], [0, 0]],
            "collinear": [[0, 0], [5, 5], [10, 10], [15, 15]],
            "three_collinear": [[0, 0], [5, 0], [10, 0], [0, 10]],
            "concave": [[0, 0], [10, 0], [2, 2], [0, 10]],
            "zero_area": [[0, 0]] * 4,
        }
        with patch("crop.cv2.getPerspectiveTransform") as transform:
            for name, points in selections.items():
                with self.subTest(selection=name):
                    self.assertIsNone(crop.extract_photo(image, points))
            transform.assert_not_called()

    def test_skewed_extraction_preserves_corner_orientation(self) -> None:
        points = [[20, 15], [140, 35], [120, 120], [10, 100]]
        image = np.zeros((141, 161, 3), dtype=np.uint8)
        colors = [(0, 0, 255), (0, 255, 0), (255, 255, 0), (255, 0, 0)]
        for (x, y), color in zip(points, colors):
            image[y - 8:y + 9, x - 8:x + 9] = color

        with patch("crop.detect_upright_rotation", return_value=0):
            extracted = crop.extract_photo(image, [points[i] for i in (2, 0, 3, 1)])

        self.assertIsNotNone(extracted)
        assert extracted is not None
        observed = [extracted[y, x] for y, x in [(2, 2), (2, -3), (-3, -3), (-3, 2)]]
        for pixel, color in zip(observed, colors):
            np.testing.assert_array_equal(pixel, color)

    def test_diamond_extraction_keeps_all_four_corner_markers(self) -> None:
        points = [[50, 0], [100, 50], [50, 100], [0, 50]]
        image = np.zeros((101, 101, 3), dtype=np.uint8)
        colors = [(0, 0, 255), (0, 255, 0), (255, 255, 0), (255, 0, 0)]
        for (x, y), color in zip(points, colors):
            image[max(0, y - 4):y + 5, max(0, x - 4):x + 5] = color

        with patch("crop.detect_upright_rotation", return_value=0):
            extracted = crop.extract_photo(image, points)

        self.assertIsNotNone(extracted)
        assert extracted is not None
        observed = [extracted[y, x] for y, x in [(2, 2), (2, -3), (-3, -3), (-3, 2)]]
        for pixel, color in zip(observed, colors):
            np.testing.assert_array_equal(pixel, color)


if __name__ == "__main__":
    unittest.main()
