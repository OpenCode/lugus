import feedparser
import markdownify
import sqlite3
import json
import webbrowser
from os import makedirs
from pathlib import Path
from textual import on, work
import textual
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, ListItem, ListView, Rule, Select, Tree, Markdown, Static, Button, \
    Label, Input, Pretty, Switch
from textual.reactive import reactive
from rich.emoji import Emoji


CONFIG_DIR = Path.joinpath(Path.home(), ".config/lugus")


class ORM():

    db = Path.joinpath(CONFIG_DIR, "lugus.db")
    connection = None

    def __init__(self) -> None:
        self.connection = sqlite3.connect(self.db, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row

    def insert(self, table, fields, values):
        cursor = self.connection.cursor()
        query = "INSERT OR IGNORE INTO %s (%s) VALUES (%s)" % (
            table,
            ", ".join(fields),
            ", ".join(["?" for _ in fields])
        )
        cursor.execute(query, values)
        self.connection.commit()

    def search(self, table, fields=None, where=None, only_one=None):
        if not fields:
            fields = ["*", ]
        cursor = self.connection.cursor()
        query = f"SELECT {",".join(fields)} FROM {table}"
        if where:
            query = f"{query} WHERE {where}"
        cursor.execute(query)
        result = cursor.fetchall()
        if only_one:
            return result[0] if result else result
        return result

    def count(self, table, where):
        cursor = self.connection.cursor()
        query = f"SELECT COUNT(id) FROM {table} WHERE {where}"
        cursor.execute(query)
        result = cursor.fetchone()
        return result[0] if result else 0

    def _convert_value(self, value):
        if isinstance(value, str):
            value = f"\"{value}\""
        return value

    def update(self, table, id_record, values):
        if isinstance(id_record, str):
            id_record = int(id_record.lower().replace("id-", ""))
        cursor = self.connection.cursor()
        cursor.execute(
            "UPDATE %s SET %s WHERE id = %s" % (
                table,
                ", ".join([
                    f"{v[0]} = {self._convert_value(v[1])}"
                    for v in values
                    ]),
                id_record
            )
        )
        self.connection.commit()

    def update_where(self, table, where, values):
        cursor = self.connection.cursor()
        cursor.execute(
            "UPDATE %s SET %s WHERE %s" % (
                table,
                ", ".join([
                    f"{v[0]} = {self._convert_value(v[1])}"
                    for v in values
                    ]),
                where,
            )
        )
        self.connection.commit()

    def read(self, table, id_record):
        if isinstance(id_record, str):
            id_record = int(id_record.lower().replace("id-", ""))
        cursor = self.connection.cursor()
        cursor.execute(f"SELECT * FROM {table} WHERE id = {id_record}")
        return cursor.fetchone()

    def create_table(self, table, fields=None):
        if not fields:
            fields = []
        if "id INTEGER" not in fields:
            fields.append(
                "id INTEGER PRIMARY KEY AUTOINCREMENT"
            )
        cursor = self.connection.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS %s (%s)" % (
                table,
                ", ".join(fields)
            )
        )

    def create_index(self, table, name, fields):
        if not fields:
            fields = []
        cursor = self.connection.cursor()
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS %s ON %s(%s)" % (
                name,
                table,
                ", ".join(fields)
            )
        )


class Config():

    orm = ORM()

    def get(self, key, default=None):
        result = self.orm.search("config", ["value", "type"], f"key = \"{key}\"", only_one=True)
        if result:
            to_return = result["value"]
            if result["type"] == "bool":
                return bool(int(to_return))
            if result["type"] == "int":
                return int(to_return)
            return to_return
        return default

    def set(self, key, value):
        if isinstance(value, int):
            vtype = "int"
        elif isinstance(value, bool):
            vtype = "bool"
            value = "1" if value else "0"
        else:
            vtype = "text"
        result = self.orm.search("config", ["id", ], f"key = \"{key}\"", only_one=True)
        if result:
            self.orm.update("config", result["id"], [("value", value)])
        else:
            self.orm.insert("config", ["key", "value", "type"], [key, value, vtype])


class Feedform(Vertical):

    def compose(self) -> ComposeResult:
        yield Label("Feed URL")
        yield Input(id="feed_url", valid_empty=False)
        yield Label("Feed Name")
        yield Input(id="feed_name", valid_empty=False)
        yield Label("Feed Group (f.e. Tech, Podcast, News, ...)")
        yield Input(id="feed_node")
        yield Button("+ Add", variant="primary", id="add_feed_button")
        yield Button("< Back", id="back_feed_button")


class ConfigurationForm(Vertical):

    config = Config()

    def compose(self) -> ComposeResult:
        yield Static("[b]Feeds\n", classes="config_area_label")
        yield Horizontal(
            Static(
                "Show Unread Counter",
                classes="config_label",
            ),
            Switch(
                id="config_feed_show_unread_counter",
                animate=True,
                classes="config_switch",
                tooltip="Show the message of articles yet to be read in the feeds tree",
                value=self.config.get("feed_show_unread_counter")
            ),
            classes="config_area_container",
        )
        yield Rule()
        yield Static("[b]Articles\n", classes="config_area_label")
        yield Horizontal(
            Static(
                "Read Automatically when Opening",
                classes="config_label",
            ),
            Switch(
                id="config_article_read_auto_opening",
                animate=True,
                classes="config_switch",
                tooltip="When an article is read in the reader, " \
                "it is automatically set as read also in the database without having to do it manually",
                value=self.config.get("article_read_auto_opening")
            ),
            classes="config_area_container",
        )
        yield Rule()
        yield Static("[b]User Interface\n", classes="config_area_label")
        yield Horizontal(
            Static(
                "Theme",
                classes="config_label"
            ),
            Select(
                [(tt, tt) for tt in textual.theme.BUILTIN_THEMES.keys()],
                id="config_ui_theme",
                value=self.config.get("ui_theme", default="textual-ansi"),
                classes="config_select"
            )
        )
        yield Button("+ Save", variant="primary", id="save_config_button")
        yield Button("< Back", id="back_feed_button")


class Feeds(Vertical):

    recomposes = reactive(0, recompose=True)

    orm = ORM()
    config = Config()

    def compose(self) -> ComposeResult:
        show_counter = self.config.get("feed_show_unread_counter")
        # Show feeds subscription
        tree: Tree[dict] = Tree("Feeds", id="feedsbar")
        tree.guide_depth = 2
        tree.root.expand()
        groups = {}
        feeds = self.orm.search("feed")
        for data in feeds:
            id_feed = data["id"]
            group_name = data["node"] or "_"
            if group_name not in groups:
                groups[group_name] = tree.root.add(group_name, expand=True)
            group = groups[group_name]
            name = data["name"]
            if show_counter:
                count = self.orm.count("article", f"feed_id = {id_feed} AND read = 0")
                if count:
                    name = f"[b]{name} ({count})"
            group.add_leaf(
                name,
                data={
                    "url": data["url"],
                    "name": data["name"],
                    "id": data["id"],
                },
            )
        yield tree
        yield Horizontal(
            Button(
                Emoji.replace(":counterclockwise_arrows_button:"),
                variant="primary",
                id="sync",
                tooltip="Get new articles from feeds",
            ),
            Button(
                Emoji.replace(":heavy_plus_sign:"),
                variant="primary",
                id="add_new",
                tooltip="Add a new feed",
            ),
            id="feed_buttons",
        )


class Articles(Vertical):

    orm = ORM()
    recomposes = reactive(0, recompose=True)
    feed = reactive(False, recompose=True)
    filter_articles_type = reactive("", recompose=True)
    filter_articles = reactive("", recompose=True)

    def compose(self) -> ComposeResult:
        articles = []
        if self.feed and self.feed.data:
            data = self.feed.data
            # Filter by Read/Unread
            if self.filter_articles_type == "unread":
                read = " AND read = 0"
            elif self.filter_articles_type == "read":
                read = " AND read = 1"
            else:
                read = ""
            # Filter by Title/Content
            if self.filter_articles:
                filters = self.filter_articles.split(",")
                filter_text = ""
                for filter in filters:
                    filter = filter.strip().replace("\"", "")
                    if filter:
                        if filter.lower().startswith("title:"):
                            filter = filter.replace("title:", "")
                            filter_text = f"{filter_text} AND title LIKE \"%{filter}%\""
                        elif filter.lower().startswith("content:"):
                            filter = filter.replace("content:", "")
                            filter_text = f"{filter_text} AND content LIKE \"%{filter}%\""
                        else:
                            filter_text = f"{filter_text} AND (title LIKE \"%{filter}%\" OR content LIKE \"%{filter}%\")"
            else:
                filter_text = ""
            articles = self.orm.search(
                "article",
                ["id", "date", "title"],
                where=f"feed_id = {data["id"]}{read}{filter_text}",
            )
        yield ListView(
            *[
                ListItem(
                    Markdown(
                        f"# {article['title']}\n"
                        f"#### {article['date']}\n"
                    ),
                    id=f"id-{article['id']}",
                )
                for article in articles
            ],
            id="articles_list",
        )


class ArticlesArea(Vertical):

    orm = ORM()

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Select(
                (
                    ("Unread", "unread"),
                    ("Read", "read"),
                    ("All", "all"),
                ),
                value="unread",
                id="filter_articles_type",
            ),
            Input(
                placeholder="Search " + Emoji.replace(":magnifying_glass_tilted_left:"),
                id="search_articles",
            ),
            classes="w100 hauto mb1",
        )
        yield Articles(
            id="articles"
        )

    @on(Select.Changed)
    def select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "filter_articles_type":
            self.query_one("#articles").filter_articles_type = event.value

    @on(Input.Changed)
    def filter_articles(self, event: Input.Changed) -> None:
        if event.input.id == "search_articles":
            self.query_one("#articles").filter_articles = event.value


class Reader(Vertical):

    orm = ORM()
    config = Config()

    id_article = reactive(False, recompose=True)
    show_raw = reactive(False, recompose=True)

    def compose(self) -> ComposeResult:
        content = ""
        raw_data = ""
        if self.id_article:
            article = self.orm.read("article", self.id_article)
            content = f"# {article['title']}\n" \
                f"#### {article['date']}\n" \
                f"## {article['subtitle']}\n" \
                f"{article['content']}\n"
            raw_data = article["raw_data"]
            raw_data = json.loads(raw_data)
            if self.config.get("article_read_auto_opening"):
                # Set the acrticle as read in the database
                self.orm.update("article", article["id"], [("read", 1)])
        if not self.show_raw:
            yield Markdown(content, id="body")
        else:
            yield Pretty(raw_data, id="article_raw_data")


class LugusApp(App):

    orm = ORM()
    config = Config()

    status = reactive("reading", recompose=True)

    BINDINGS = [
        ("c", "open_configuration", "Configs"),
        ("question_mark", "article_data", "Article Raw Data"),
        ("r", "article_read", "Set Article as Read"),
        ("a", "all_articles_read", "Set all Feed Articles as Read"),
        ("o", "read_online", "Read Online"),
    ]

    CSS_PATH = "style.tcss"

    def on_mount(self) -> None:
        self.theme = self.config.get("ui_theme", "textual-ansi")

    def compose(self) -> ComposeResult:
        # Show Header
        yield Header(
            id="header",
        )
        if self.status == "reading":
            # Show feeds subscription
            yield Feeds(id="sidebar")
            # Show Articles
            yield ArticlesArea(id="articles_area")
            # Show Article Reader
            yield Reader(id="reader")
            #Show Footer
            yield Footer()
        elif self.status == "adding_feed":
            yield Feedform(id="feed_form")
        elif self.status == "configuration":
            yield ConfigurationForm(id="configuration_form")

    @work(exclusive=True, thread=True)
    def _synchronize_feeds(self, update_interface_element=None):
        if update_interface_element:
            update_interface_element.disabled = True
            update_interface_element.label = "0/0 Feeds"
        feeds = self.orm.search("feed", ["id", "url"])
        if update_interface_element:
            total_feeds = len(feeds)
            update_interface_element.label = f"0/{total_feeds} Feeds"
        for actual, feed in enumerate(feeds, start=1):
            if update_interface_element:
                update_interface_element.label = f"{actual}/{total_feeds} Feeds"
            feed_data = feedparser.parse(feed["url"])
            for article in feed_data.entries:
                if article.get("content"): 
                    content = "\n\n".join([
                        markdownify.markdownify(content.value, heading_style="ATX")
                        for content in article.get("content", [article.link, ])
                        if content.type in ("text/text", "text/html")
                    ])
                elif article.get("summary"):
                    content = markdownify.markdownify(article.summary)
                else:
                    content = markdownify.markdownify(article.link)
                self.orm.insert(
                    "article",
                    (
                        "url", "title", "subtitle", "summary", "date", "read", "content",
                        "original_id", "raw_data", "feed_id"
                    ),
                    (
                        article.link,
                        article.get("title", article.link),
                        article.get("subtitle"),
                        article.get("summary"),
                        article.get("published", article.get("updated", "")),
                        0,
                        content,
                        article.id,
                        json.dumps(article, indent=4),
                        feed["id"],
                    ),
                )
        if update_interface_element:
            update_interface_element.disabled = False
            update_interface_element.label = Emoji.replace(":counterclockwise_arrows_button:")
            self.notify("Feeds synchronized!")
        return

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sync":
            self._synchronize_feeds(update_interface_element=event.button)
        elif event.button.id == "add_new":
            self.status = "adding_feed"
        elif event.button.id == "back_feed_button":
            self.status = "reading"
        elif event.button.id == "add_feed_button":
            self.orm.insert(
                "feed",
                ("url", "name", "node"),
                (
                    self.query_one("#feed_url").value,
                    self.query_one("#feed_name").value,
                    self.query_one("#feed_node").value,
                )
            )
            self.status = "reading"
            self.notify("Feed Added!")
        elif event.button.id == "save_config_button":
            self.config.set(
                "feed_show_unread_counter",
                self.query_one("#config_feed_show_unread_counter").value
            )
            self.config.set(
                "article_read_auto_opening",
                self.query_one("#config_article_read_auto_opening").value
            )
            self.config.set(
                "ui_theme",
                self.query_one("#config_ui_theme").value
            )
            self.theme = self.config.get("ui_theme")
            self.status = "reading"

    def on_list_view_selected(self, event: ListView.Selected):
        self.query_one("#reader").id_article = event.item.id
        # Trigger recompose of the feedbar to show the counters updated
        self.query_one("#sidebar").recomposes += 1

    def on_tree_node_selected(self, event: Tree.NodeSelected):
        self.query_one("#articles").feed = event.node

    def action_article_data(self) -> None: 
        reader = self.query_one("#reader")
        reader.show_raw = not reader.show_raw

    def action_article_read(self) -> None:
        articles = self.query_one("#articles_list")
        if articles.highlighted_child:
            # id_article = int(articles.highlighted_child.id.replace("id-", ""))
            id_article = articles.highlighted_child.id
            self.orm.update("article", id_article, [("read", 1)])
            self.query_one("#articles").recomposes += 1
            self.notify("Article set as Read")

    def action_open_configuration(self) -> None:
        self.status = "configuration"

    def action_all_articles_read(self) -> None:
        feed = self.query_one("#articles").feed
        if not feed:
            self.notify("Select a feed", severity="warning")
        else:
            self.orm.update_where(
                "article",
                f"feed_id = {feed.data["id"]} AND read = 0",
                [("read", 1)],
            )
            self.query_one("#sidebar").recomposes += 1
            self.query_one("#articles").recomposes += 1

    def action_read_online(self) -> None:
        reader = self.query_one("#reader")
        if not reader.id_article:
            self.notify("Select an article!", severity="warning")
        else:
            article = self.orm.read("article", reader.id_article)
            webbrowser.open(article["url"])


def set_environment():
    makedirs(CONFIG_DIR, exist_ok=True)
    orm = ORM()
    orm.create_table("config", ["key TEXT", "value TEXT", "type TEXT"])
    orm.create_table(
        "feed",
        [
            "url TEXT NOT NULL",
            "name TEXT NOT NULL",
            "node TEXT",
        ]
    )
    orm.create_index("feed", "feed_idx", ["url"])
    orm.create_table(
        "article",
        [
            "url TEXT NOT NULL",
            "title TEXT NOT NULL",
            "subtitle TEXT",
            "summary TEXT",
            "date TEXT",
            "read INTEGER",
            "content TEXT",
            "original_id TEXT",
            "raw_data TEXT",
            "feed_id INTEGER",
        ]
    )
    orm.create_index("article", "article_idx", ["original_id", "url", "feed_id"])


def run() -> None:
    set_environment()
    app = LugusApp()
    app.title = "Lugus"
    app.sub_title="A minimal feeds reader"
    app.run()


if __name__ == "__main__":
    run()

