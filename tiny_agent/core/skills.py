"""
    tiny_agent.core.skills
    ~~~~~~~~~~~~~~~~~~~~~~

    Discovers and loads skill files from the skills directory.
    Each skill lives in its own subfolder as a SKILL.md file.
    Content is trimmed to a size cap before being injected into
    the conversation context.

"""

from pathlib import Path


SKILLS_DIR = "~/.tinyagent/skills/"
MAX_CONTENT_SIZE = 4096


class SkillsManager(object):
    """Discovers available skills and loads their content on demand.
    Tracks which skills have already been loaded to prevent duplicates."""

    def __init__(self, skills_dir=None):
        self.skills_dir = Path(skills_dir or SKILLS_DIR).expanduser()
        self.loaded = set()

    def list_skills(self):
        """Return sorted names of available skills.

        Scans the skills directory for subdirectories that contain
        a SKILL.md file.

        Returns:
            list: Skill names found in the skills directory.
        """

        if not self.skills_dir.is_dir():
            return []

        return sorted(
            d.name for d in self.skills_dir.iterdir()
            if d.is_dir() and (d / "SKILL.md").is_file()
        )

    def load_skill(self, name):
        """Read a skill's SKILL.md and return its content.

        Args:
            name (str): Name of the skill folder to load.

        Returns:
            tuple: (content, path, truncated) where content is the
                skill text, path is the absolute file path, and
                truncated is True if the content was trimmed.

        Raises:
            FileNotFoundError: If the skill directory or SKILL.md
                does not exist.
        """

        path = (self.skills_dir / name / "SKILL.md").resolve()

        # Guard against path traversal (e.g. "../other")
        if not str(path).startswith(str(self.skills_dir.resolve())):
            raise FileNotFoundError(f"Skill '{name}' not found")

        if not path.is_file():
            raise FileNotFoundError(f"Skill '{name}' not found")

        content = path.read_text()
        truncated = len(content) > MAX_CONTENT_SIZE

        if truncated:
            content = content[:MAX_CONTENT_SIZE]

        self.loaded.add(name)
        return content, str(path), truncated
