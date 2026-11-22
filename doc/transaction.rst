Transactions
============

.. automodule:: svgwrite.transaction

A transaction groups several edits of a :class:`svgwrite.Drawing` into one
atomic unit. Edits are applied live while the transaction is open (so the
existing fluent API and read-your-writes access keep working), and are either
all kept on :meth:`~svgwrite.transaction.Transaction.commit` or all undone on
:meth:`~svgwrite.transaction.Transaction.rollback` -- the latter also happens
automatically when an exception escapes the ``with`` block.

Basic example
-------------

Editing a level icon usually touches the ``defs`` section, the layer tree and
the references in between. Without transactions a failure halfway through
would leave the document in an inconsistent state::

    try:
        with dwg.transaction():
            symbol = dwg.defs.add(dwg.symbol(id='level-icon'))
            symbol.add(dwg.rect(insert=(0, 0), size=(10, 10)))
            use = dwg.add(dwg.use(symbol, insert=(20, 20)))
            dwg.groups_layer['data-level'] = '3'
            validate(dwg)          # if this raises, everything above is undone
    except ValidationError:
        ...  # dwg is back in its original state

The same transaction can be driven manually::

    txn = dwg.transaction().begin()
    try:
        ...
        txn.commit()
    except Exception:
        txn.rollback()
        raise

What gets rolled back
---------------------

* attribute writes and deletions, including direct container access such as
  ``element.attribs['fill'] = 'red'`` or ``del element.attribs['id']``
* structural changes through :meth:`~svgwrite.base.BaseElement.add`,
  :meth:`~svgwrite.base.BaseElement.remove`, ``set_desc``/``set_metadata`` and
  direct ``elements`` list manipulation (append, insert, pop, slices, ...)
* references: changing an element's ``id`` and refreshing
  :class:`~svgwrite.container.Use` links restores the original ``xlink:href``
  values
* mutable state outside the attribute dictionary -- ``Path.commands``,
  ``Polyline.points``, ``Script._content`` and the drawing's stylesheets
* IDs handed out by :class:`~svgwrite.utils.AutoID`: the counter is restored,
  so a retried transaction produces exactly the IDs a fresh run would

Elements that existed before the transaction keep their identity; only their
containers are restored. Elements created inside a rolled-back transaction are
detached from the tree again.

Concurrency rules
-----------------

* one open transaction per document -- a second attempt, also from another
  thread, raises :class:`~svgwrite.transaction.TransactionError`
* nested transactions are not supported and raise
  :class:`~svgwrite.transaction.TransactionError`
* mutations from a different thread while a transaction is open raise
  :class:`~svgwrite.transaction.TransactionError`; transactions on different
  documents are independent
* different documents may be edited concurrently, each by its own transaction
* with no transaction open, all APIs behave exactly as in previous versions

.. autoclass:: svgwrite.transaction.Transaction
   :members: begin, commit, rollback, closed

.. autoclass:: svgwrite.transaction.TransactionError
   :members:
