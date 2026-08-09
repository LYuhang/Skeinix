from vibecanvas_api.services.preview_resource_policy import (
    html_vfs_read_rules,
    rules_for_root,
)


def test_html_resource_rules_are_exact_and_dynamic_paths_use_static_directory():
    rules = html_vfs_read_rules(
        """
        <img src="/data/images/cover.png?rev=2">
        <a href="/mount/data/report.pdf">Report</a>
        <script>
          const frame = `/data/dataset/images/${index}.png`;
          fetch('/data/labels/current.json');
          fetch('/data/../logs/private.json');
          fetch('https://example.com/public.png');
        </script>
        <div style="background:url(/run/results/chart.svg)"></div>
        """
    )
    assert rules == (
        "/data/dataset/images/",
        "/data/images/cover.png",
        "/data/labels/current.json",
        "/mount/data/report.pdf",
        "/run/results/chart.svg",
    )
    assert rules_for_root(rules, "mount") == ("/mount/data/report.pdf",)


def test_html_resource_rules_reject_traversal_and_non_vfs_urls():
    assert html_vfs_read_rules(
        """
        <img src="/data/../memory/private.txt">
        <img src="https://example.com/x">
        <script>
          fetch(`/data/${userControlledPath}`);
          fetch(`/mount/${userControlledPath}`);
          fetch(`/run/${userControlledPath}`);
        </script>
        """
    ) == ()
