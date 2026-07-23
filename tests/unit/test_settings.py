from pathlib import Path

import pytest

from morning_radar.settings import (
    AppConfig,
    CompanyConfig,
    PersonConfig,
    RepositoryConfig,
    SourceConfig,
    TopicConfig,
    load_model,
    load_model_list,
)


def test_repository_configuration_files_are_valid() -> None:
    app = load_model(Path("config/app.yaml"), AppConfig)
    sources = load_model_list(Path("config/sources.yaml"), "sources", SourceConfig)
    topics = load_model_list(Path("config/topics.yaml"), "topics", TopicConfig)
    companies = load_model_list(Path("config/companies.yaml"), "companies", CompanyConfig)
    repositories = load_model_list(
        Path("config/repositories.yaml"),
        "repositories",
        RepositoryConfig,
    )
    people = load_model_list(Path("config/people.yaml"), "people", PersonConfig)

    assert app.timezone == "Asia/Singapore"
    assert app.collection_buffer_hours == 6
    assert any(source.official for source in sources)
    assert topics and companies and repositories and people


def test_configuration_error_names_file_and_field(tmp_path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("news_window_hours: nope\n", encoding="utf-8")

    with pytest.raises(ValueError) as error:
        load_model(path, AppConfig)

    assert str(path) in str(error.value)
    assert "news_window_hours" in str(error.value)
