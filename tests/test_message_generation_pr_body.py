"""Unit tests for format_pr_body_with_metadata PR-line rendering (ST-4167).

Covers the multi-PR batch listing introduced for connection merge-queue
deploys, the single-PR fallback, and the edge cases flagged in review
(missing repository_url must not auto-link to the wrong repo; non-int /
bool entries must be filtered).
"""

from helm_image_updater.message_generation import format_pr_body_with_metadata


def _metadata(**source_overrides):
    """Build pipeline metadata with a sensible source block, overridable per test."""
    source = {
        "repository": "keboola/connection",
        "repository_url": "https://github.com/keboola/connection",
        "workflow_url": "https://github.com/keboola/connection/actions/runs/1",
        "sha": "d049a945387e242f2e58379a964b4758bd076343",
        "actor": "bot",
        "timestamp": "2026-06-26T00:00:00Z",
    }
    source.update(source_overrides)
    return {"source": source}


def _pr_line(body: str) -> str:
    """Return the single '- **Pull Request(s):**' line from a rendered body, or ''."""
    for line in body.splitlines():
        if "Pull Request" in line:
            return line.strip()
    return ""


class TestFormatPrBodyPrLine:
    """PR-line rendering in format_pr_body_with_metadata."""

    def test_multiple_pr_numbers_render_linked_list(self):
        """A batch renders every PR as a linked #<n>, with a plural label."""
        body = format_pr_body_with_metadata(
            "connection", "production-abc",
            _metadata(pr_numbers=[7008, 6999, 7001]),
        )
        line = _pr_line(body)
        assert line.startswith("- **Pull Requests:**")
        assert "[#7008](https://github.com/keboola/connection/pull/7008)" in line
        assert "[#6999](https://github.com/keboola/connection/pull/6999)" in line
        assert "[#7001](https://github.com/keboola/connection/pull/7001)" in line
        # comma-separated
        assert line.count(", ") == 2

    def test_single_pr_number_uses_singular_label(self):
        """A one-element batch uses the singular 'Pull Request' label."""
        body = format_pr_body_with_metadata(
            "connection", "production-abc",
            _metadata(pr_numbers=[7008]),
        )
        line = _pr_line(body)
        assert line.startswith("- **Pull Request:**")
        assert "[#7008](https://github.com/keboola/connection/pull/7008)" in line

    def test_falls_back_to_pr_url_when_no_pr_numbers(self):
        """Without pr_numbers, fall back to the single source.pr_url line."""
        body = format_pr_body_with_metadata(
            "connection", "production-abc",
            _metadata(pr_url="https://github.com/keboola/connection/pull/7008"),
        )
        line = _pr_line(body)
        assert line == (
            "- **Pull Request:** "
            "[https://github.com/keboola/connection/pull/7008]"
            "(https://github.com/keboola/connection/pull/7008)"
        )

    def test_no_pr_line_when_neither_present(self):
        """No pr_numbers and no pr_url -> no PR line at all."""
        body = format_pr_body_with_metadata(
            "connection", "production-abc", _metadata(),
        )
        assert _pr_line(body) == ""

    def test_missing_repository_url_uses_non_linking_code_span(self):
        """Without repository_url, render `#<n>` code spans, never a bare #<n>
        (which GitHub would auto-link to the wrong repo where the body is posted)."""
        body = format_pr_body_with_metadata(
            "connection", "production-abc",
            _metadata(repository_url="", pr_numbers=[7008, 6999]),
        )
        line = _pr_line(body)
        assert "`#7008`" in line
        assert "`#6999`" in line
        # no markdown links were built
        assert "](http" not in line
        # and not a bare, auto-linkable "#7008" (it must be inside backticks)
        assert " #7008" not in line.replace("`#7008`", "")

    def test_pr_numbers_take_precedence_over_pr_url(self):
        """When both are present, the batch list wins over the single pr_url."""
        body = format_pr_body_with_metadata(
            "connection", "production-abc",
            _metadata(
                pr_numbers=[7008, 6999],
                pr_url="https://github.com/keboola/connection/pull/7008",
            ),
        )
        line = _pr_line(body)
        assert line.startswith("- **Pull Requests:**")
        assert "[#6999](https://github.com/keboola/connection/pull/6999)" in line

    def test_filters_non_int_and_bool_entries(self):
        """Strings, floats, and booleans in pr_numbers are dropped; ints kept."""
        body = format_pr_body_with_metadata(
            "connection", "production-abc",
            _metadata(pr_numbers=[7008, True, "6999", 1.5, 7001]),
        )
        line = _pr_line(body)
        assert "[#7008](https://github.com/keboola/connection/pull/7008)" in line
        assert "[#7001](https://github.com/keboola/connection/pull/7001)" in line
        # bool True (== 1) must not become "#1"; "6999" string and 1.5 float dropped
        assert "#1)" not in line and "#1`" not in line
        assert "#6999" not in line
        assert "#1.5" not in line

    def test_trailing_slash_in_repository_url_is_normalized(self):
        """A trailing slash on repository_url does not double up in the PR link."""
        body = format_pr_body_with_metadata(
            "connection", "production-abc",
            _metadata(repository_url="https://github.com/keboola/connection/",
                      pr_numbers=[7008]),
        )
        line = _pr_line(body)
        assert "[#7008](https://github.com/keboola/connection/pull/7008)" in line
        assert "//pull/" not in line
