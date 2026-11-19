Reference Audit
===============

.. automodule:: svgwrite.audit

A reference audit walks the complete document tree -- the ``defs`` section,
nested groups and shared subtrees included -- and checks every internal
reference:

* ``id`` attributes (empty and duplicate ids),
* ``xlink:href`` / ``href`` attributes of ``use``, ``tref``, ``textPath``,
  ``feImage``, gradients, patterns and filters,
* ``url(#id)`` FuncIRI values in presentation attributes (``fill``,
  ``stroke``, ``clip-path``, ``mask``, ``filter``, ``marker*`` ...),
  in ``style`` attributes and inside the CSS text of ``style`` elements.

Usage
-----

Audit a drawing and inspect the report::

    import svgwrite

    dwg = svgwrite.Drawing('icon.svg')
    dwg.add(dwg.rect(fill='url(#blue-gradient)'))  # gradient never defined
    report = dwg.audit_references()
    if not report.ok:
        for finding in report:
            print(finding.severity, finding.code, finding.path, finding.message)

Or refuse to export a broken document directly -- in strict mode
:meth:`~svgwrite.drawing.Drawing.save` raises
:class:`ReferenceAuditError` *before* the output file is opened::

    dwg.save(audit=True)

Forward references (the referenced element appears later, typically inside
``defs``) are legal, because the whole tree is indexed before any reference
is resolved.  External URLs such as ``http://example.com/a.png``,
``other.svg#frag`` or ``data:...`` are legal as well and never reported.

Strict and non-strict mode
--------------------------

``strict=True`` (the default) reports every defect as an error.  In
non-strict mode (``strict=False``) ambiguous but often renderable cases are
only warnings, while genuinely unresolvable references remain errors:

===================================== =============== ===============
finding                               strict          non-strict
===================================== =============== ===============
``missing-reference`` / ``empty-*``   error           error
``use-cycle``                         error           error
``empty-id`` / ``duplicate-id``       error           warning
``type-mismatch``                     error           warning
``escaped-url``                       error           warning
``max-depth-exceeded``                error           warning
===================================== =============== ===============

Type checks
-----------

The audit knows which element types each reference expects, e.g. ``fill``
and ``stroke`` require a ``linearGradient``, ``radialGradient``,
``pattern`` or ``solidColor``; ``clip-path`` requires a ``clipPath``;
``marker*`` requires a ``marker``.  A reference that resolves but points at
the wrong element type is reported as ``type-mismatch``.

``use`` cycles
--------------

Circular ``use`` chains -- including cycles that run through nested groups
(a ``use`` inside a group pointing back at an ancestor) -- are reported as
``use-cycle``.  Legitimate sharing, where several ``use`` elements
reference the same target, creates no cycle and is never flagged.

Finding paths
-------------

Every finding carries a deterministic element path such as
``svg[0]/defs[0]/linearGradient[1]/@fill``: element names with their
child index, followed by the offending attribute (``@fill``), an inline
style property (``@style/fill``) or a CSS rule inside a ``style`` element
(``::style/fill``).

API
---

.. autofunction:: audit_references

.. autoclass:: ReferenceAuditor
   :members: audit

.. autoclass:: AuditReport
   :members: ok, errors, warnings, raise_if_errors, findings

.. autoclass:: Finding
   :members: code, severity, message, path, ref

.. autoexception:: ReferenceAuditError
   :members: report
