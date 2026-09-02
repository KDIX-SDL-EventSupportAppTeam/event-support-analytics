"""`event-support-recommend` の features/ を import する seam の確認。

正本は向こうの `src/event_support_recommend/features/`。
**定義をこちらへコピーしない**（event-support-server ADR 0004）ため、
「見つからないときに黙って自前実装へ落ちない」ことをここで担保する。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import rec_features  # noqa: E402


def _make_fake_repo(root: Path) -> Path:
    """推薦リポジトリと同じ src レイアウトの最小構成を作る。"""
    pkg = root / "src" / "event_support_recommend" / "features"
    pkg.mkdir(parents=True)
    (root / "src" / "event_support_recommend" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("MARKER = 'from-recommend-repo'\n", encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _clean_module_state(monkeypatch):
    monkeypatch.delenv(rec_features.ENV_PATH, raising=False)
    for name in list(sys.modules):
        if name.startswith("event_support_recommend"):
            monkeypatch.delitem(sys.modules, name, raising=False)


@pytest.mark.parametrize("subdir", ["", "src"])
def test_accepts_repo_root_or_src(tmp_path, monkeypatch, subdir):
    """リポジトリのルートでも src/ でも受ける（src レイアウトの吸収）。"""
    repo = _make_fake_repo(tmp_path / "event-support-recommend")
    monkeypatch.setenv(rec_features.ENV_PATH, str(repo / subdir if subdir else repo))
    mod = rec_features.load_features()
    assert mod.MARKER == "from-recommend-repo"
    assert mod.__name__ == rec_features.MODULE


def test_missing_features_raises_with_actionable_hint(tmp_path, monkeypatch):
    """見つからないとき、**黙って自前実装に落ちない**（ADR 0004）。"""
    monkeypatch.setenv(rec_features.ENV_PATH, str(tmp_path))
    with pytest.raises(ModuleNotFoundError) as exc:
        rec_features.load_features()
    assert "event_support_recommend/features/" in str(exc.value)
    assert rec_features.ENV_PATH in str(exc.value)


def test_features_available_is_false_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv(rec_features.ENV_PATH, str(tmp_path))
    assert rec_features.features_available() is False


def test_features_available_is_true_when_present(tmp_path, monkeypatch):
    repo = _make_fake_repo(tmp_path / "event-support-recommend")
    monkeypatch.setenv(rec_features.ENV_PATH, str(repo))
    assert rec_features.features_available() is True
