import os

class CrawlerFactory:
    """
    Factory class to manage and instantiate different league crawlers.
    """
    def __init__(self, config_path=None):
        self.config_path = config_path
        self.registry = {}

    def register_crawler(self, league_id, crawler_class):
        """Registers a crawler class for a specific league."""
        self.registry[league_id] = crawler_class
        print(f"Registered crawler for: {league_id}")

    def get_crawler(self, league_id, *args, **kwargs):
        """Returns an instance of the registered crawler."""
        crawler_class = self.registry.get(league_id)
        if not crawler_class:
            raise ValueError(f"No crawler registered for league: {league_id}")
        return crawler_class(*args, **kwargs)

if __name__ == "__main__":
    # Simple test of the factory
    class MockCrawler:
        def __init__(self, name):
            self.name = name
        def crawl(self):
            return f"Crawling {self.name}..."

    factory = CrawlerFactory()
    factory.register_crawler("csl", MockCrawler)
    crawler = factory.get_crawler("csl", name="CSL_Primary")
    print(crawler.crawl())
