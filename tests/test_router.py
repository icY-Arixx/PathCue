import unittest

from urban_mobility_rag.router import IntentRouter


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.router = IntentRouter()

    def test_route_plan(self):
        self.assertEqual(self.router.route("从学校到火车站怎么走"), "route_plan")

    def test_poi(self):
        self.assertEqual(self.router.route("附近哪里有充电站"), "poi_search")

    def test_knowledge(self):
        self.assertEqual(self.router.route("POI数据如何去重"), "knowledge")


if __name__ == "__main__":
    unittest.main()

