"""Common Neo4j connection and explicit transactions for source-specific graph stores."""

from contextlib import contextmanager

from neo4j import GraphDatabase

from app.core.config import Settings


class Neo4jStore:
    def __init__(self, settings: Settings):
        if not settings.neo4j_password:
            raise ValueError("请配置 NEO4J_PASSWORD")
        self.driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            connection_timeout=10,
            max_transaction_retry_time=15,
        )
        self.database = settings.neo4j_database

    def close(self):
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def transaction(self):
        with self.driver.session(database=self.database) as session:
            with session.begin_transaction(timeout=120) as tx:
                yield tx
                tx.commit()

    def read(self, cypher, **params):
        with self.driver.session(database=self.database) as session:
            return session.execute_read(lambda tx: tx.run(cypher, parameters=params).data())
