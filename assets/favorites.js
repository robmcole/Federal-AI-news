/* Favorites live in localStorage on this browser only. */
(function () {
  var KEY = "fan-favorites-v1";

  function siteRoot() {
    var script = document.querySelector("script[src*='favorites.js']");
    if (!script) return new URL("./", window.location.href);
    return new URL("../", script.src);
  }

  function load() {
    try {
      var raw = localStorage.getItem(KEY);
      var data = raw ? JSON.parse(raw) : {};
      return data && typeof data === "object" ? data : {};
    } catch (err) {
      return {};
    }
  }

  function persist(data) {
    try {
      localStorage.setItem(KEY, JSON.stringify(data));
    } catch (err) {
      /* Private mode or a full disk. The button state still updates. */
    }
  }

  function initials(source) {
    var words = String(source || "").match(/[A-Za-z0-9]+/g) || [];
    if (!words.length) return "AI";
    if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
    return (words[0][0] + words[1][0]).toUpperCase();
  }

  function label(on, headline) {
    return (on ? "Remove saved story: " : "Save story: ") + headline;
  }

  function setButton(button, on, headline) {
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.setAttribute("aria-label", label(on, headline));
  }

  function thumbUrl(path) {
    if (!path || !/^media\/thumbs\/[a-f0-9]+\.jpg$/.test(path)) return "";
    return new URL(path, siteRoot()).href;
  }

  function safeUrl(url) {
    if (typeof url !== "string") return "";
    if (url.indexOf("https://") === 0 || url.indexOf("http://") === 0) return url;
    return "";
  }

  function star() {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("width", "18");
    svg.setAttribute("height", "18");
    var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute(
      "d",
      "M12 3.2l2.55 5.17 5.7.83-4.12 4.02.97 5.68L12 16.2l-5.1 2.7.97-5.68L3.75 9.2l5.7-.83L12 3.2z"
    );
    svg.appendChild(path);
    return svg;
  }

  function renderFavorites(data) {
    var list = document.getElementById("fav-list");
    var empty = document.getElementById("fav-empty");
    if (!list || !empty) return;
    var items = Object.keys(data).map(function (id) {
      return data[id];
    });
    items.sort(function (a, b) {
      return String(b.savedAt || "").localeCompare(String(a.savedAt || ""));
    });
    list.replaceChildren();
    empty.hidden = items.length > 0;
    items.forEach(function (item) {
      if (!item || !item.id || !safeUrl(item.url)) return;
      list.appendChild(renderCard(item));
    });
    if (!list.children.length) empty.hidden = false;
  }

  function renderCard(item) {
    var card = document.createElement("article");
    card.className = "story is-saved";
    card.dataset.storyId = item.id;
    card.dataset.story = JSON.stringify(item);

    var media = document.createElement("a");
    media.className = "story-media";
    media.href = item.url;
    media.target = "_blank";
    media.rel = "noopener noreferrer";
    media.setAttribute("aria-label", "Open story: " + (item.headline || "story"));
    var src = thumbUrl(item.thumbnail);
    if (src) {
      var img = document.createElement("img");
      img.src = src;
      img.alt = "";
      img.width = 720;
      img.height = 450;
      img.loading = "lazy";
      img.decoding = "async";
      media.appendChild(img);
    } else {
      media.classList.add("is-fallback");
      var mark = document.createElement("span");
      mark.className = "fallback-initials";
      mark.textContent = initials(item.source);
      var outlet = document.createElement("span");
      outlet.className = "fallback-outlet";
      outlet.textContent = item.source || "Source";
      media.appendChild(mark);
      media.appendChild(outlet);
    }

    var body = document.createElement("div");
    body.className = "story-body";
    if (item.issueLabel) {
      var when = document.createElement("p");
      when.className = "story-when";
      when.textContent = item.issueLabel;
      body.appendChild(when);
    }
    var title = document.createElement("h3");
    title.className = "item-title";
    var titleLink = document.createElement("a");
    titleLink.href = item.url;
    titleLink.target = "_blank";
    titleLink.rel = "noopener noreferrer";
    titleLink.textContent = item.headline || item.url;
    title.appendChild(titleLink);
    body.appendChild(title);
    if (item.summary) {
      var summary = document.createElement("p");
      summary.className = "story-summary";
      summary.textContent = item.summary;
      body.appendChild(summary);
    }
    var foot = document.createElement("div");
    foot.className = "story-foot";
    var meta = document.createElement("p");
    meta.className = "item-meta";
    var source = document.createElement("span");
    source.className = "source-name";
    source.textContent = "Source: " + (item.source || "Unknown");
    meta.appendChild(source);
    if (item.score) {
      var badge = document.createElement("span");
      badge.className = "score score-" + item.score;
      var k = document.createElement("span");
      k.className = "score-k";
      k.textContent = "Reliability Score:";
      badge.appendChild(k);
      badge.appendChild(document.createTextNode(" " + item.score + "/5"));
      meta.appendChild(badge);
    }
    var button = document.createElement("button");
    button.type = "button";
    button.className = "fav-btn";
    button.appendChild(star());
    setButton(button, true, item.headline || "story");
    foot.appendChild(meta);
    foot.appendChild(button);
    body.appendChild(foot);
    card.appendChild(media);
    card.appendChild(body);
    return card;
  }

  function updateCounts(data) {
    var count = Object.keys(data).length;
    document.querySelectorAll("[data-fav-count]").forEach(function (el) {
      el.hidden = count === 0;
      el.textContent = String(count);
    });
  }

  function applyButtons(data) {
    document.querySelectorAll(".story[data-story-id]").forEach(function (card) {
      if (card.closest("#fav-list")) return;
      var on = Boolean(data[card.dataset.storyId]);
      card.classList.toggle("is-saved", on);
      var button = card.querySelector(".fav-btn");
      if (!button) return;
      var story = {};
      try {
        story = JSON.parse(card.getAttribute("data-story") || "{}");
      } catch (err) {
        story = {};
      }
      setButton(button, on, story.headline || "story");
    });
  }

  function applyFilter() {
    var digest = document.querySelector(".digest");
    var button = document.querySelector(".filter-favs");
    if (!digest || !button) return;
    var on = button.getAttribute("aria-pressed") === "true";
    digest.classList.toggle("show-saved", on);
    var node = digest.firstElementChild;
    while (node) {
      if (node.tagName === "H2") {
        var sibling = node.nextElementSibling;
        var sawStory = false;
        var sawSaved = false;
        while (sibling && sibling.tagName !== "H2") {
          if (sibling.classList && sibling.classList.contains("story")) {
            sawStory = true;
            if (sibling.classList.contains("is-saved")) sawSaved = true;
          }
          sibling = sibling.nextElementSibling;
        }
        node.hidden = on && sawStory && !sawSaved;
      } else if (
        node.tagName === "UL" ||
        (node.classList && node.classList.contains("score-legend"))
      ) {
        node.hidden = on;
      }
      node = node.nextElementSibling;
    }
    var empty = digest.querySelector(".filter-empty");
    if (empty) empty.hidden = !(on && !digest.querySelector(".story.is-saved"));
  }

  function refresh() {
    var data = load();
    applyButtons(data);
    updateCounts(data);
    renderFavorites(data);
    applyFilter();
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest(".fav-btn");
    if (!button) return;
    var card = button.closest(".story");
    if (!card) return;
    var story = {};
    try {
      story = JSON.parse(card.getAttribute("data-story") || "{}");
    } catch (err) {
      return;
    }
    if (!story.id) return;
    var data = load();
    var on = !data[story.id];
    if (on) {
      story.savedAt = new Date().toISOString();
      data[story.id] = story;
    } else {
      delete data[story.id];
    }
    persist(data);
    refresh();
  });

  var filter = document.querySelector(".filter-favs");
  if (filter) {
    filter.addEventListener("click", function () {
      var on = filter.getAttribute("aria-pressed") !== "true";
      filter.setAttribute("aria-pressed", on ? "true" : "false");
      filter.textContent = on ? "Showing saved only" : "Show saved only";
      applyFilter();
    });
  }

  window.addEventListener("pageshow", refresh);
  window.addEventListener("storage", refresh);
  refresh();
})();
