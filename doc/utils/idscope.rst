idscope module
==============

.. automodule:: svgwrite.idscope

.. autoclass:: svgwrite.idscope.IDScope

.. automethod:: svgwrite.idscope.IDScope.allocate

.. automethod:: svgwrite.idscope.IDScope.request

.. automethod:: svgwrite.idscope.IDScope.release

.. automethod:: svgwrite.idscope.IDScope.detach_tree

.. automethod:: svgwrite.idscope.IDScope.adopt_tree

.. attribute:: IDScope.conflicts

   `tuple` of :class:`~svgwrite.idscope.IDConflict` entries describing every
   resolved ID conflict.

.. attribute:: IDScope.ids

   `tuple` of all IDs currently registered in the scope, in alphabetical
   order.

.. autoclass:: svgwrite.idscope.IDConflict

.. autoclass:: svgwrite.idscope.IDConflictError
