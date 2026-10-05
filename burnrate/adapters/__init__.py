"""Adapters turn a raw data source into the common format (see burnrate.schema).

Each adapter is small and does one thing: read one kind of log, return one
DataFrame with the standard columns. Add a new source by adding a file here —
nothing else in the project has to change.
"""
