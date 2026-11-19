Image
=====

.. autoclass:: svgwrite.image.Image

Methods
-------

.. automethod:: svgwrite.image.Image.__init__

.. automethod:: svgwrite.image.Image.stretch

.. automethod:: svgwrite.image.Image.fit

.. automethod:: svgwrite.image.Image.embed

Embedding local images
======================

An ``<image>`` element normally links to a separate file. If the SVG document
is moved or opened on another machine without that file, the bitmap is
missing. :meth:`svgwrite.Drawing.embed_images` replaces every local PNG/JPEG
reference by a base64 encoded ``data:`` URI, so the document is
self-contained::

    import svgwrite

    drawing = svgwrite.Drawing('standalone.svg')
    drawing.add(drawing.image('icons/logo.png', insert=(10, 10),
                              size=(32, 32), opacity=0.5,
                              transform='rotate(10)'))
    drawing.embed_images(root='assets')
    drawing.save()

The ``root`` argument is the explicit document root for relative references;
paths which resolve outside of it are rejected. ``x``, ``y``, ``width``,
``height``, ``transform`` and ``opacity`` are not touched. Remote URLs
(``http://``, ``https://``, protocol relative URLs, ...) and already embedded
``data:`` URIs are skipped. A file referenced several times is read only
once, and with ``deduplicate=True`` (the default) content-identical files share
one data URI.

Problems raise an :class:`svgwrite.embed.EmbedError` subclass:

* ``DocumentRootError`` - relative reference without a root (also a
  ``ValueError``)
* ``AssetNotFoundError`` - missing file (also a ``FileNotFoundError``)
* ``PathTraversalError`` - resolved path outside the document root
* ``AssetTypeError`` / ``UnsupportedAssetError`` - extension and content do
  not agree or the type is not PNG/JPEG
* ``AssetTooLargeError`` - file larger than ``max_size`` (10 MiB by default)
* ``CircularReferenceError`` - a symbolic link loop, a cycle in the element
  tree, or an image which refers to the SVG document itself

Use ``strict=False`` to collect the problems in the returned
:class:`svgwrite.embed.EmbedReport` and leave the affected references
unchanged instead. Embedding is opt-in: ``write``, ``save`` and ``tostring``
keep their usual behaviour when ``embed_images`` is not called.

Parent Classes
--------------

* :class:`svgwrite.base.BaseElement`
* :class:`svgwrite.mixins.Transform`
* :class:`svgwrite.mixins.Clipping`

SVG Attributes
--------------
* **class** -- `string`

  assigns one or more css-class-names to an element

* **style** -- `string`

  allows per-element css-style rules to be specified directly on a given
  element

* **externalResourcesRequired** -- `bool`

  *False*: if document rendering can proceed even if external resources are
  unavailable else: *True*

* **transform** -- use :class:`svgwrite.mixins.Transform` interface

* **x** -- `<coordinate>` -- **insert** parameter

  The x-axis coordinate of one corner of the rectangular region into which
  the referenced document is placed.

  Default is ``'0'``.

* **y** -- `<coordinate>` -- **insert** parameter

  The y-axis coordinate of one corner of the rectangular region into which
  the referenced document is placed.

  Default is ``'0'``.

* **width** -- `<length>` -- **size** parameter

  The width of the rectangular region into which the referenced document is
  placed. A negative value is an error. A value of zero disables rendering
  of the element.

* **height** -- `<length>` -- **size** parameter

  The height of the rectangular region into which the referenced document is
  placed. A negative value is an error. A value of zero disables rendering of
  the element.

* **xlink:href** -- `string` -- **href** parameter

  A IRI reference to the image resource.

* **preserveAspectRatio** -- ``'[defer] <align> [<meetOrSlice>]'``

Standard SVG Attributes
-----------------------

* :doc:`Core Attributes </attributes/core>`
* :doc:`Conditional Processing Attributes </attributes/conditional_processing>`
* :doc:`Graphical Event Attributes </attributes/graphical_event>`
* :doc:`Presentation Attributes </attributes/presentation>`
* :doc:`XLink Attributes </attributes/xlink>`
