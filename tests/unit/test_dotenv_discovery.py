"""`.env` is found regardless of the launcher's working directory.

find_dotenv(usecwd=True) walks UP from the current working directory.
That works for `flask run` from the project root, and fails silently for
a PyCharm run configuration whose working directory is the repository
root while the application lives in a subdirectory -- the file sits
BELOW the search, never above it, so nothing is loaded and every
variable falls back to its default.

The symptom is not an error. It is a setting that refuses to take effect
no matter how many times the file is corrected, which is indis-
tinguishable from the value being wrong.
"""
import os

from app.config import _dotenv_candidates


def _names(paths):
    return [os.path.normpath(p) for p in paths]


class TestDotenvCandidates:
    def test_includes_the_package_relative_env(self):
        """The decisive one: derived from config.py's OWN location.

        This is what makes discovery independent of the launcher. The
        file lives next to the application package, so locating it from
        the source file always works -- whatever the working directory
        happens to be.
        """
        candidates = _names(_dotenv_candidates())
        expected = os.path.normpath(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
                __import__("app.config", fromlist=["config"]).__file__))),
                ".env"))
        assert expected in candidates

    def test_package_relative_path_points_beside_the_app_package(self):
        """Guards the ".." count.

        config.py is app/config.py, so the project root is TWO levels up
        from the file, not one. Off by one here would look correct in a
        listing and find nothing at runtime.
        """
        import app.config as cfg
        root = os.path.dirname(os.path.dirname(os.path.abspath(cfg.__file__)))
        assert os.path.isdir(os.path.join(root, "app"))
        assert os.path.normpath(os.path.join(root, ".env")) in _names(
            _dotenv_candidates())

    def test_package_relative_candidate_is_searched_last(self):
        """Order matters and is not arbitrary.

        A deliberate .env in the working directory should win over the
        one beside the package, so a developer can still point the app
        at a different file by launching from elsewhere. The
        package-relative path is the FALLBACK, so it must come last.

        Asserted as a POSITION rather than a count: when the app is
        launched from its own root both candidates are the same file and
        collapse to one entry, so any assertion on length would pass or
        fail depending only on where pytest was invoked from.
        """
        import app.config as cfg
        root = os.path.dirname(os.path.dirname(os.path.abspath(cfg.__file__)))
        package_relative = os.path.normpath(os.path.join(root, ".env"))
        candidates = _names(_dotenv_candidates())
        assert candidates[-1] == package_relative

    def test_returns_absolute_paths(self):
        """Relative paths would re-introduce the cwd dependency this
        function exists to remove."""
        for path in _dotenv_candidates():
            assert os.path.isabs(path), path

    def test_no_duplicates(self):
        """When the app IS launched from its own root both candidates
        resolve to the same file; loading it twice is harmless but the
        duplicate makes the list misleading to read in a debugger."""
        candidates = _names(_dotenv_candidates())
        assert len(candidates) == len(set(candidates))
