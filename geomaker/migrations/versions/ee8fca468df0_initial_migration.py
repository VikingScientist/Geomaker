"""initial migration

Revision ID: ee8fca468df0
Revises: 
Create Date: 2020-02-17 17:06:04.086497

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ee8fca468df0'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('datafile',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('filename', sa.String(), nullable=False),
    sa.Column('type', sa.String(), nullable=True),
    sa.Column('coords', sa.String(), nullable=False),
    sa.Column('east', sa.Float(), nullable=False),
    sa.Column('west', sa.Float(), nullable=False),
    sa.Column('south', sa.Float(), nullable=False),
    sa.Column('north', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('polygon',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('job',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('polygon_id', sa.Integer(), nullable=True),
    sa.Column('project', sa.String(), nullable=False),
    sa.Column('dedicated', sa.Boolean(), nullable=False),
    sa.Column('jobid', sa.Integer(), nullable=False),
    sa.Column('stage', sa.String(), nullable=False),
    sa.Column('error', sa.String(), nullable=True),
    sa.Column('url', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['polygon_id'], ['polygon.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('point',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('x', sa.Float(), nullable=False),
    sa.Column('y', sa.Float(), nullable=False),
    sa.Column('polygon_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['polygon_id'], ['polygon.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('polydata',
    sa.Column('polygon_id', sa.Integer(), nullable=False),
    sa.Column('datafile_id', sa.Integer(), nullable=False),
    sa.Column('dedicated', sa.Boolean(), nullable=False),
    sa.Column('project', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['datafile_id'], ['datafile.id'], ),
    sa.ForeignKeyConstraint(['polygon_id'], ['polygon.id'], ),
    sa.PrimaryKeyConstraint('polygon_id', 'datafile_id')
    )
    op.create_table('thumbnail',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('filename', sa.String(), nullable=False),
    sa.Column('project', sa.String(), nullable=False),
    sa.Column('polygon_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['polygon_id'], ['polygon.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('thumbnail')
    op.drop_table('polydata')
    op.drop_table('point')
    op.drop_table('job')
    op.drop_table('polygon')
    op.drop_table('datafile')
    # ### end Alembic commands ###