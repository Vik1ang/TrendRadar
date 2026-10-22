import unittest

from p4_relay_memory import parse_docker_memory_usage


class DockerMemoryUsageTests(unittest.TestCase):
    def test_parses_docker_stats_attached_mib_and_ignores_limit(self) -> None:
        self.assertEqual(parse_docker_memory_usage("20.16MiB / 755.3GiB"), 20.16)

    def test_converts_gib_and_bytes_to_mib(self) -> None:
        self.assertEqual(parse_docker_memory_usage("1.5GiB / 8GiB"), 1536.0)
        self.assertEqual(parse_docker_memory_usage("1048576B / 8GiB"), 1.0)

    def test_rejects_unrecognized_or_empty_output(self) -> None:
        for value in ("", "20 widget / 8 GiB", "not-a-number / 8GiB"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_docker_memory_usage(value)


if __name__ == "__main__":
    unittest.main()
