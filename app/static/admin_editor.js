(function () {
  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function inlineMarkdownToHtml(value) {
    return escapeHtml(value)
      .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1">')
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  function markdownToHtml(markdown) {
    var lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
    var html = [];
    var paragraph = [];
    var listType = null;
    var code = [];
    var inCode = false;
    var quote = [];

    function flushParagraph() {
      if (paragraph.length) {
        html.push("<p>" + inlineMarkdownToHtml(paragraph.join(" ")) + "</p>");
        paragraph = [];
      }
    }

    function flushList() {
      if (listType) {
        html.push("</" + listType + ">");
        listType = null;
      }
    }

    function flushQuote() {
      if (quote.length) {
        html.push("<blockquote><p>" + inlineMarkdownToHtml(quote.join(" ")) + "</p></blockquote>");
        quote = [];
      }
    }

    lines.forEach(function (line) {
      var trimmed = line.trim();

      if (trimmed.indexOf("```") === 0) {
        flushParagraph();
        flushList();
        flushQuote();
        if (inCode) {
          html.push("<pre><code>" + escapeHtml(code.join("\n")) + "</code></pre>");
          code = [];
          inCode = false;
        } else {
          inCode = true;
        }
        return;
      }

      if (inCode) {
        code.push(line);
        return;
      }

      if (!trimmed) {
        flushParagraph();
        flushList();
        flushQuote();
        return;
      }

      var heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
      if (heading) {
        flushParagraph();
        flushList();
        flushQuote();
        html.push("<h" + heading[1].length + ">" + inlineMarkdownToHtml(heading[2]) + "</h" + heading[1].length + ">");
        return;
      }

      var unordered = trimmed.match(/^[-*]\s+(.+)$/);
      var ordered = trimmed.match(/^\d+\.\s+(.+)$/);
      if (unordered || ordered) {
        flushParagraph();
        flushQuote();
        var nextType = unordered ? "ul" : "ol";
        if (listType !== nextType) {
          flushList();
          html.push("<" + nextType + ">");
          listType = nextType;
        }
        html.push("<li>" + inlineMarkdownToHtml((unordered || ordered)[1]) + "</li>");
        return;
      }

      if (trimmed.indexOf("> ") === 0) {
        flushParagraph();
        flushList();
        quote.push(trimmed.slice(2));
        return;
      }

      paragraph.push(trimmed);
    });

    flushParagraph();
    flushList();
    flushQuote();
    if (inCode) {
      html.push("<pre><code>" + escapeHtml(code.join("\n")) + "</code></pre>");
    }
    return html.join("\n");
  }

  function textContent(node) {
    return (node.textContent || "").replace(/\u00a0/g, " ").trim();
  }

  function inlineHtmlToMarkdown(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      return node.nodeValue || "";
    }
    if (node.nodeType !== Node.ELEMENT_NODE) {
      return "";
    }

    var tag = node.tagName.toLowerCase();
    var children = Array.prototype.map.call(node.childNodes, inlineHtmlToMarkdown).join("");

    if (tag === "strong" || tag === "b") return "**" + children + "**";
    if (tag === "em" || tag === "i") return "*" + children + "*";
    if (tag === "code") return "`" + children + "`";
    if (tag === "a") return "[" + children + "](" + (node.getAttribute("href") || "") + ")";
    if (tag === "img") return "![" + (node.getAttribute("alt") || "Image") + "](" + (node.getAttribute("src") || "") + ")";
    if (tag === "br") return "\n";
    return children;
  }

  function blockToMarkdown(node, listPrefix) {
    if (node.nodeType === Node.TEXT_NODE) {
      return textContent(node);
    }
    if (node.nodeType !== Node.ELEMENT_NODE) {
      return "";
    }

    var tag = node.tagName.toLowerCase();
    if (/^h[1-6]$/.test(tag)) {
      return "#".repeat(Number(tag.slice(1))) + " " + inlineHtmlToMarkdown(node).trim();
    }
    if (tag === "p" || tag === "div") {
      return inlineHtmlToMarkdown(node).trim();
    }
    if (tag === "blockquote") {
      return inlineHtmlToMarkdown(node).trim().split("\n").map(function (line) {
        return "> " + line;
      }).join("\n");
    }
    if (tag === "pre") {
      return "```\n" + textContent(node) + "\n```";
    }
    if (tag === "ul" || tag === "ol") {
      return Array.prototype.map.call(node.children, function (child, index) {
        var prefix = tag === "ol" ? (index + 1) + ". " : "- ";
        return blockToMarkdown(child, prefix);
      }).join("\n");
    }
    if (tag === "li") {
      return (listPrefix || "- ") + inlineHtmlToMarkdown(node).trim();
    }
    if (tag === "img") {
      return inlineHtmlToMarkdown(node);
    }
    return inlineHtmlToMarkdown(node).trim();
  }

  function htmlToMarkdown(root) {
    return Array.prototype.map.call(root.childNodes, function (node) {
      return blockToMarkdown(node);
    }).filter(Boolean).join("\n\n").trim();
  }

  function runCommand(command, value) {
    document.execCommand(command, false, value || null);
  }

  function insertHtml(html) {
    document.execCommand("insertHTML", false, html);
  }

  function initialiseEditor(editor) {
    var visual = editor.querySelector("[data-editor-visual]");
    var source = editor.querySelector("[data-editor-source]");
    var modeButtons = editor.querySelectorAll("[data-editor-mode]");
    var toolbarButtons = editor.querySelectorAll("[data-editor-command], [data-editor-action]");
    var form = editor.closest("form");
    var mode = "visual";

    function syncToVisual() {
      visual.innerHTML = markdownToHtml(source.value);
    }

    function syncToSource() {
      source.value = htmlToMarkdown(visual);
    }

    function setMode(nextMode) {
      if (nextMode === mode) return;
      if (nextMode === "source") {
        syncToSource();
      } else {
        syncToVisual();
      }
      mode = nextMode;
      editor.dataset.mode = mode;
      modeButtons.forEach(function (button) {
        button.classList.toggle("active", button.dataset.editorMode === mode);
      });
    }

    function focusVisual() {
      if (mode !== "visual") setMode("visual");
      visual.focus();
    }

    syncToVisual();
    editor.dataset.mode = mode;

    modeButtons.forEach(function (button) {
      button.addEventListener("click", function () {
        setMode(button.dataset.editorMode);
      });
    });

    toolbarButtons.forEach(function (button) {
      button.addEventListener("click", function () {
        focusVisual();
        var command = button.dataset.editorCommand;
        var action = button.dataset.editorAction;

        if (command) {
          runCommand(command, button.dataset.editorValue);
          return;
        }

        if (action === "link") {
          var url = window.prompt("Link URL");
          if (url) runCommand("createLink", url);
        }

        if (action === "image") {
          var imageUrl = window.prompt("Image URL or upload path");
          if (imageUrl) insertHtml('<img src="' + escapeHtml(imageUrl) + '" alt="Image">');
        }

        if (action === "code") {
          insertHtml("<pre><code>paste command or config here</code></pre><p></p>");
        }

        if (action === "quote") {
          runCommand("formatBlock", "blockquote");
        }
      });
    });

    document.querySelectorAll("[data-editor-insert-image]").forEach(function (button) {
      button.addEventListener("click", function () {
        focusVisual();
        insertHtml('<img src="' + escapeHtml(button.dataset.editorInsertImage) + '" alt="Uploaded image">');
      });
    });

    if (form) {
      form.addEventListener("submit", function () {
        if (mode === "visual") {
          syncToSource();
        }
      });
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-rich-editor]").forEach(initialiseEditor);
  });
}());
