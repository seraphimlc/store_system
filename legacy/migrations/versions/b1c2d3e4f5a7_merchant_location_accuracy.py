"""Add location accuracy to the local merchant source and remove Google ID imports."""
from alembic import op
import sqlalchemy as sa

revision = "b1c2d3e4f5a7"
down_revision = "b0a1c2d3e4f6"
branch_labels = None
depends_on = None

TABLE = "kanto_merchants_v20261029"


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    tables = set(sa.inspect(bind).get_table_names(schema="public"))
    if TABLE not in tables:
        return
    columns = {col["name"] for col in sa.inspect(bind).get_columns(TABLE, schema="public")}
    if "location_accuracy" not in columns:
        op.add_column(TABLE, sa.Column("location_accuracy", sa.Text(), nullable=False,
                                       server_default=sa.text("'pending'")), schema="public")
    for column in ("google_place_candidates", "google_place_status", "google_place_id"):
        if column in columns:
            op.drop_column(TABLE, column, schema="public")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    tables = set(sa.inspect(bind).get_table_names(schema="public"))
    if TABLE not in tables:
        return
    columns = {col["name"] for col in sa.inspect(bind).get_columns(TABLE, schema="public")}
    if "location_accuracy" in columns:
        op.drop_column(TABLE, "location_accuracy", schema="public")
