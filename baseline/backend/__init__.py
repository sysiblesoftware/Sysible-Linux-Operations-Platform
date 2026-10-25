"""Sysible Baseline — desired-state configuration management for the fleet.

A SLOP-native module. Baseline declares what a host's configuration must be,
Salt makes it so (masterless, via the Controller's existing agent channel — see
backend/salt.py), and event rules decide what follows a change (backend/eda.py).

Baseline is NOT a backup tool. What a host's files actually look like, as
opposed to what Baseline declared they should, is Sysible Flashback's job — a
separate service with a separate store. The declaration and the observation have
to be able to disagree, or neither is worth reading.
"""
