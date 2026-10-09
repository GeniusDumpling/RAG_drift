from app.models.supplier import SupplierRelation, SupplierVerification
from app.repositories.database import _search_condition, get_table_config, list_table_metadata
from sqlalchemy.dialects import postgresql


def test_supplier_tables_are_browsable_and_searchable():
    metadata = {item.table_name for item in list_table_metadata()}
    for model in (SupplierRelation, SupplierVerification):
        name = model.__tablename__
        assert name in metadata
        config = get_table_config(name)
        assert config.model is model
        assert "supplier_name" in config.preview_columns
        for column in (*config.preview_columns, *config.detail_columns,
                       *config.searchable_columns, config.default_sort_column):
            assert column in model.__table__.columns
        condition = _search_condition(config, "test%_")
        compiled = condition.compile(dialect=postgresql.dialect())
        assert "supplier_name ILIKE" in str(compiled)
        assert set(compiled.params.values()) == {"%test\\%\\_%"}
    assert get_table_config("supplier_relations; DROP TABLE source_sites") is None
