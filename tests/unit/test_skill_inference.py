"""Skill inference (hooks/skills.py): the bare skill name from ``skill_view`` arguments
and ``/skills/`` paths, and the skill directory from evidence only (#147, #157)."""

from hermes_otel.hooks.skills import (
    detect_skill,
    infer_skill_name,
    infer_skill_name_from_text,
    resolve_skill_dir,
)


class TestInferSkillName:
    def test_matches_skills_path(self):
        assert infer_skill_name({"path": "/home/user/skills/monitor/SKILL.md"}) == "monitor"

    def test_matches_skills_path_no_trailing(self):
        # Just /skills/name with no trailing slash should still match if end of string
        assert infer_skill_name({"path": "/a/b/skills/foo"}) == "foo"

    def test_does_not_match_optional_skills_references(self):
        assert infer_skill_name({"path": "/optional-skills/monitor/references/x.md"}) is None

    def test_file_path_key(self, tmp_path):
        skill = tmp_path / "skills" / "builder"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: builder\n---\n")
        assert infer_skill_name({"file_path": str(skill / "tool.py")}) == "builder"

    def test_target_key(self, tmp_path):
        skill = tmp_path / "skills" / "deployer"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: deployer\n---\n")
        assert infer_skill_name({"target": str(skill / "README")}) == "deployer"

    def test_file_inside_unverifiable_skill_is_not_named(self):
        # No SKILL.md reachable: flat or categorized cannot be told apart (#157).
        assert infer_skill_name({"file_path": "/x/skills/builder/tool.py"}) is None

    def test_categorized_skill_manifest_resolves_to_bare_name(self):
        # Hermes names skills by the directory holding SKILL.md, never the category.
        assert (
            infer_skill_name(
                {"path": "/home/user/skills/software-development/code-review/SKILL.md"}
            )
            == "code-review"
        )

    def test_windows_categorized_skill_manifest_resolves_to_bare_name(self):
        assert (
            infer_skill_name(
                {"path": r"C:\Users\me\skills\software-development\code-review\SKILL.md"}
            )
            == "code-review"
        )

    def test_categorized_directory_reference_uses_filesystem(self, tmp_path):
        skill_dir = tmp_path / "skills" / "software-development" / "code-review"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: code-review\n---\n")
        assert infer_skill_name({"path": str(skill_dir)}) == "code-review"
        # Without a filesystem hit the text is ambiguous: no name, not the category (#157).
        assert infer_skill_name({"path": "/nope/skills/software-development/code-review"}) is None

    def test_file_inside_categorized_skill_uses_filesystem(self, tmp_path):
        # references/x.md gives no textual clue whether the layout is flat or
        # categorized; the nearest ancestor that holds SKILL.md decides.
        skill_dir = tmp_path / "skills" / "software-development" / "code-review"
        (skill_dir / "references").mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: code-review\n---\n")
        ref = skill_dir / "references" / "checklist.md"
        assert infer_skill_name({"path": str(ref)}) == "code-review"

    def test_file_inside_skill_without_filesystem_is_not_guessed(self):
        # A flat layout would make "monitor" right and a categorized one wrong;
        # the text cannot tell, so nothing is reported (#157).
        assert infer_skill_name({"path": "/nope/skills/monitor/references/x.md"}) is None

    def test_skill_view_name_forms_all_reduce_to_bare_name(self):
        for form in (
            "code-review",
            "software-development/code-review",
            "software-development:code-review",
            "hermes_otel:code-review",
        ):
            assert detect_skill("skill_view", {"name": form}) == ("code-review", "skill_view")

    def test_no_path_returns_none(self):
        assert infer_skill_name({"command": "ls"}) is None

    def test_no_match_returns_none(self):
        assert infer_skill_name({"path": "/tmp/regular/file.txt"}) is None

    def test_none_args(self):
        assert infer_skill_name(None) is None

    def test_infer_from_text_helper(self):
        assert infer_skill_name_from_text("cat /skills/hello/SKILL.md") == "hello"
        assert infer_skill_name_from_text("cat /skills/hello") == "hello"
        # A file inside an unverifiable skill directory is not guessed (#157).
        assert infer_skill_name_from_text("cat /skills/hello/x") is None

    def test_infer_from_text_miss(self):
        assert infer_skill_name_from_text("no match here") is None

    def test_infer_from_text_non_string(self):
        assert infer_skill_name_from_text(42) is None


class TestResolveSkillDir:
    """hermes.skill.path comes from evidence only (#147)."""

    def test_manifest_path_yields_its_parent_as_written(self):
        assert (
            resolve_skill_dir(
                {"path": "/h/.hermes/skills/software-development/code-review/SKILL.md"}
            )
            == "/h/.hermes/skills/software-development/code-review"
        )
        assert resolve_skill_dir({"file_path": "skills/monitor/skill.md"}) == "skills/monitor"

    def test_windows_manifest_path(self):
        assert (
            resolve_skill_dir({"path": r"C:\Users\me\skills\code-review\SKILL.md"})
            == "C:/Users/me/skills/code-review"
        )

    def test_file_inside_skill_resolves_via_filesystem(self, tmp_path):
        skill_dir = tmp_path / "skills" / "software-development" / "code-review"
        (skill_dir / "references").mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: code-review\n---\n")
        ref = skill_dir / "references" / "checklist.md"
        assert resolve_skill_dir({"path": str(ref)}) == str(skill_dir)

    def test_plugin_bundled_skill_resolves_to_its_real_directory(self, tmp_path):
        skill_dir = tmp_path / "plugins" / "hermes_otel" / "skills" / "observability"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: observability\n---\n")
        assert resolve_skill_dir({"path": str(skill_dir / "SKILL.md")}) == str(skill_dir)

    def test_unverifiable_paths_yield_none_not_a_guess(self):
        # A categorized path with no manifest on disk: the name is ambiguous and
        # the directory is unknown; nothing is invented.
        assert resolve_skill_dir({"path": "/nope/skills/software-development/code-review"}) is None
        assert resolve_skill_dir({"path": "/nope/skills/monitor/references/x.md"}) is None
        assert resolve_skill_dir({"command": "ls"}) is None
        assert resolve_skill_dir({"path": "/tmp/regular/file.txt"}) is None
        assert resolve_skill_dir(None) is None


class TestDetectSkill:
    def test_skill_view_name_arg(self):
        assert detect_skill("skill_view", {"name": "axolotl"}) == ("axolotl", "skill_view")

    def test_skill_view_skill_arg(self):
        assert detect_skill("skill_view", {"skill": "deploy"}) == ("deploy", "skill_view")

    def test_skill_view_strips_plugin_namespace(self):
        assert detect_skill("skill_view", {"name": "myplugin:foo"}) == ("foo", "skill_view")

    def test_path_match_on_read(self):
        got = detect_skill("read", {"path": "/h/.hermes/skills/bar/SKILL.md"})
        assert got == ("bar", "path_match")

    def test_skill_view_with_manifest_path_value(self):
        got = detect_skill("skill_view", {"name": "x/.hermes/skills/baz/SKILL.md"})
        assert got == ("baz", "skill_view")

    def test_skill_view_with_unverifiable_path_value_is_not_guessed(self):
        # Neither the category nor the file name is a skill name (#157).
        assert detect_skill("skill_view", {"name": "x/.hermes/skills/baz/refs.md"}) == (None, None)

    def test_non_skill_tool(self):
        assert detect_skill("bash", {"command": "ls"}) == (None, None)

    def test_non_dict_args(self):
        assert detect_skill("skill_view", None) == (None, None)
