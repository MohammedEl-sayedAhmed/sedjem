<!-- What changes and why, in a few sentences, and the issue it closes ("Closes #N"). -->

- [ ] The commit titles start with the area they change ("Updates: ...", "Website: ..."), since the release notes
      are drafted from them.
- [ ] Tests come with the change, and `python -m unittest discover -s tests` passes.
- [ ] A changed requirement file comes with its locks: `python .github/scripts/locks.py` (docs/14-pipeline.md).
- [ ] The docs say what changed, in plain prose (docs/14-pipeline.md lists the rules for public text).
- [ ] Nothing private: no recordings, transcripts or keys, and no names or paths from a personal computer.

By sending a change, you agree that it may be distributed under both the AGPL and the commercial licence (see
Licence in README.md).
