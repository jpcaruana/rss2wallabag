#!/usr/bin/env python
# coding=utf-8
import asyncio
import logging
import sys
from time import mktime
from urllib.parse import urljoin

import aiohttp
import feedparser
import sentry_sdk
import yaml
from wallabag_api.wallabag import Wallabag

import github_stars

logger = logging.getLogger()
logger.handlers = []
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
logger.setLevel(logging.DEBUG)

with open("config.yaml", 'r') as stream:
    try:
        config = yaml.safe_load(stream)
    except (yaml.YAMLError, FileNotFoundError) as exception:
        config = None
        exit(1)

production = "debug" not in config or not config["debug"]

ch = logging.StreamHandler(stream=sys.stdout)
ch.setLevel(logging.WARNING if production else logging.DEBUG)
ch.setFormatter(formatter)
logger.addHandler(ch)

fh = logging.FileHandler('debug.log')
fh.setFormatter(formatter)
fh.setLevel(logging.WARNING if production else logging.DEBUG)
logger.addHandler(fh)

if "sentry_url" in config and (production):
    sentry_sdk.init(dsn=config["sentry_url"])

with open("sites.yaml", 'r') as stream:
    try:
        sites = yaml.safe_load(stream)
    except (yaml.YAMLError, FileNotFoundError) as exception:
        logger.error(exception)
        sites = None
        exit(1)


async def fetch(session, url):
    try:
        async with session.get(url) as response:
            return await response.text()
    except Exception as e:
        logging.exception("failed to fetch {url}".format(url=url))


async def main(loop, sites):
    token = await Wallabag.get_token(**config["wallabag"])

    async with aiohttp.ClientSession(loop=loop) as session:
        wall = Wallabag(host=config["wallabag"]["host"], client_secret=config["wallabag"]["client_secret"],
                        client_id=config["wallabag"]["client_id"], token=token, aio_sess=session)

        if "github_username" in config:
            sites = github_stars.get_starred_repos(config["github_username"], sites)

        await asyncio.gather(*[handle_feed(session, wall, sitetitle, site) for sitetitle, site in sites.items()])


async def handle_feed(session, wall, sitetitle, site):
    logger.info("Downloading feed: " + sitetitle)
    rss = await fetch(session, site["url"])
    logger.info("Parsing feed: " + sitetitle)
    f = feedparser.parse(rss)
    logger.debug("finished parsing: " + sitetitle)
    # feedtitle = f["feed"]["title"]
    if "latest_article" in site:
        for article in f.entries:
            if article.title == site["latest_article"]:
                logger.debug("already added: " + article.title)
                break
            logger.info("article found: " + article.title)
            taglist = [sitetitle]
            if site["tags"]:
                taglist.extend(site["tags"])
            tags = ",".join(taglist)
            if "published_parsed" in article:
                published = mktime(article.published_parsed)
            elif "updated_parsed" in article:
                published = mktime(article.updated_parsed)
            else:
                published = None
            logger.info("add to wallabag: " + article.title)
            if "github" in site and site["github"]:
                title = sitetitle + ": " + article.title
            else:
                title = article.title
            if not hasattr(article, 'link'):
                logger.info("no link, skipping!")
                continue
            url = urljoin(site["url"], article.link)
            exists = await wall.entries_exists(url)
            if exists["exists"]:
                logger.info("already found in wallabag: " + article.title)
            if production:
                await wall.post_entries(url=url, title=title, tags=tags)
            else:
                logger.info("warning: running in debug mode - not adding links to wallabag")
    else:
        logger.debug("no latest_article: " + sitetitle)
    if f.entries:
        sites[sitetitle]["latest_article"] = f.entries[0].title


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main(loop, sites))
    if production:
        with open("sites.yaml", 'w') as stream:
            yaml.dump(sites, stream, default_flow_style=False)
