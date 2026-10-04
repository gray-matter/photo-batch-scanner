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

    @unittest.expectedFailure
    def test_diamond_orders_four_distinct_cyclic_corners(self) -> None:
        points = np.array([[50, 0], [100, 50], [50, 100], [0, 50]], dtype=np.float32)

        ordered = crop.order_quad_points(points)

        np.testing.assert_array_equal(ordered, points)

    @unittest.expectedFailure
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
