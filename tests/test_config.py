import config


class TestLoadEnvFile:
    def test_loads_pairs_and_ignores_comments(self, tmp_path):
        path = tmp_path / '.env'
        path.write_text(
            '# comment\n\nA=1\nexport FANTASYPROS_API_KEY="abc"\n'
        )
        target = {}
        loaded = config.load_env_file(str(path), environ=target)

        assert target == {'A': '1', 'FANTASYPROS_API_KEY': 'abc'}
        assert loaded == {'A': '1', 'FANTASYPROS_API_KEY': 'abc'}

    def test_existing_environment_wins(self, tmp_path):
        path = tmp_path / '.env'
        path.write_text('KEY=from-file\n')
        target = {'KEY': 'from-env'}

        config.load_env_file(str(path), environ=target)

        assert target['KEY'] == 'from-env'

    def test_missing_file_is_a_noop(self, tmp_path):
        target = {}

        assert config.load_env_file(str(tmp_path / 'missing'), environ=target) == {}
        assert target == {}

    def test_quotes_and_blank_values_are_handled(self, tmp_path):
        path = tmp_path / '.env'
        path.write_text("A='one'\nB=\"two\"\nC=\n")
        target = {}

        config.load_env_file(str(path), environ=target)

        assert target == {'A': 'one', 'B': 'two', 'C': ''}
