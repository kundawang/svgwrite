Merging Drawings
================

.. automodule:: svgwrite.merge

Why merging is needed
---------------------

Every :class:`~svgwrite.drawing.Drawing` has its own ID space. Two drawings
may define a ``linearGradient`` or a ``clipPath`` with the same ``id`` and
reference them internally via ``url(#id)`` or ``xlink:href="#id"``. Putting
both element trees into one SVG document naively makes those IDs collide: the
definitions overwrite each other and the references resolve to the wrong
objects.

:meth:`Drawing.merge` solves this by wrapping every source drawing in an
independent group (a nested ``<svg>`` by default) and moving every source ID
into a private namespace.

Basic usage
-----------

::

    import svgwrite

    def icon(color):
        dwg = svgwrite.Drawing(size=(60, 60))
        grad = dwg.defs.add(dwg.linearGradient(id='paint'))
        grad.add_stop_color(0, 'white').add_stop_color(1, color)
        clip = dwg.defs.add(dwg.clipPath(id='shape'))
        clip.add(dwg.circle(center=(30, 30), r=25))
        dwg.add(dwg.rect(size=(60, 60), fill='url(#paint)',
                         clip_path='url(#shape)'))
        return dwg

    red, blue = icon('red'), icon('blue')

    combined = svgwrite.Drawing(size=(120, 60))
    combined.merge(red, insert=(0, 0))
    combined.merge(blue, insert=(60, 0))

The resulting document contains ``merge1_paint`` / ``merge1_shape`` and
``merge2_paint`` / ``merge2_shape``, and every internal reference points at
the matching namespaced ID::

    <svg ...>
      <defs />
      <svg height="60" id="merge1" width="60" x="0" y="0">
        <defs>
          <linearGradient id="merge1_paint"> ... </linearGradient>
          <clipPath id="merge1_shape"> ... </clipPath>
        </defs>
        <rect clip-path="url(#merge1_shape)"
              fill="url(#merge1_paint)" ... />
      </svg>
      <svg height="60" id="merge2" width="60" x="60" y="0"> ... </svg>
    </svg>

Several drawings can be merged in one call. Without an explicit prefix each
one gets an automatic ``mergeN`` namespace; with a shared prefix the second
and following sources receive ``_2``, ``_3`` ... suffixes::

    combined.merge_all([red, blue])
    combined.merge([red, blue], prefix='icon')  # icon_*, icon_2_*

What gets rewritten
-------------------

* ``href`` and ``xlink:href`` fragment references (``<use>``, gradient /
  pattern / filter inheritance, ...)
* ``url(#id)`` functional references in presentation attributes such as
  ``fill``, ``stroke``, ``clip-path``, ``filter``, ``mask`` and the
  ``marker*`` attributes
* ``url(#id)`` inside embedded ``<style>`` / ``<script>`` text
* element references in SMIL timing attributes (``begin``, ``end``,
  ``repeatBegin``, ``repeatEnd``)

External URLs (``http://...``, relative file names, ``data:...``) and
fragments that do not resolve inside the source drawing are never modified.

Preserved properties
--------------------

* the source ``<defs>`` section, moved into the wrapper group
* the drawing order of all elements
* width, height and ``viewBox`` (nested ``<svg>`` wrapper); pass
  ``as_nested_svg=False`` to use a plain ``<g>`` instead
* transforms and presentation attributes on the source root
* source drawings themselves - they are deep copied, stay usable and can be
  merged again; merged output does not depend on the global
  :class:`~svgwrite.utils.AutoID` counter and is deterministic

Validation
----------

In strict mode (the default) the first problem raises one of the
:class:`MergeError` subclasses and the target drawing is not modified. With
``strict=False`` the problems are collected in the returned
:class:`MergeReport` and repairable issues are repaired automatically::

    report = combined.merge(drawing, strict=False)
    if not report.ok:
        for issue in report.issues:
            print(issue.kind, issue.message)

API
---

.. autoclass:: svgwrite.merge.DrawingMerger
    :members:

.. autoclass:: svgwrite.merge.MergeReport
    :members:

.. autoclass:: svgwrite.merge.Issue
    :members:

.. autofunction:: svgwrite.merge.is_valid_id

Exceptions
----------

.. autoexception:: svgwrite.merge.MergeError
.. autoexception:: svgwrite.merge.DuplicateIdError
.. autoexception:: svgwrite.merge.InvalidIdError
.. autoexception:: svgwrite.merge.NamespaceCollisionError
.. autoexception:: svgwrite.merge.MissingReferenceError
.. autoexception:: svgwrite.merge.CircularReferenceError
