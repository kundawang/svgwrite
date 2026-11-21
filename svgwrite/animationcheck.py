#!/usr/bin/env python
# coding:utf-8
# Purpose: quality assurance for SMIL animation elements
# Created: 2026-11-22
# License: MIT License
"""
Validation for the SMIL animation constructs created by
:mod:`svgwrite.animate`.

The SVG syntax of two mutually waiting animations is perfectly legal::

    <animate id="a" begin="b.begin" ... />
    <animate id="b" begin="a.begin" ... />

but no SMIL player will ever start them: every *begin* is waiting on an event
that can never fire.  The same happens when a *begin*/*end* reference points
at a missing element, or when a startup chain never reaches an element that
starts on its own (document begin, clock offset, DOM event, ...).

This module inspects the in-memory svgwrite element tree *before*
serialization and reports concrete defects, without changing the generated
markup:

* ``missing-timing-reference``   - *begin*/*end* points at an unknown id
* ``invalid-timing-reference``   - syncbase/repeat points at a non-animation
  element (it can never raise begin/end/repeat events)
* ``invalid-timing-syntax``      - timing value that cannot be parsed
* ``circular-timing-dependency`` - element is part of a begin-reference cycle
* ``unreachable-animation``      - no startup chain reaches the element
* ``invalid-attribute-combination`` - values/keyTimes/keySplines/dur/
  repeatCount boundary violations

Entry point::

    from svgwrite.animationcheck import check_animations
    report = check_animations(drawing)
    if not report.ok:
        for finding in report.findings:
            print(finding)
"""

from svgwrite.data.svgparser import event_names
import re

__all__ = ['Finding', 'AnimationReport', 'check_animations']

ANIMATION_ELEMENTS = frozenset((
    'set', 'animate', 'animateColor', 'animateMotion', 'animateTransform',
))

# kinds of timing entries that can make an animation start without waiting on
# another SMIL animation (user/script/document driven roots of the timeline)
EXTERNAL_KINDS = frozenset(('indefinite', 'event', 'accesskey', 'wallclock'))

# ---------------------------------------------------------------------------
# low level parsers
# ---------------------------------------------------------------------------

_UNIT_FACTORS = {'ms': 0.001, 's': 1.0, 'min': 60.0, 'h': 3600.0}

_CLOCK_RE = re.compile(
    r'^\s*(?:'
    r'(?P<full>\d{2}):(?P<full_mm>\d{2}):(?P<full_ss>\d+(?:\.\d+)?)$'
    r'|(?P<partial>\d{2}):(?P<partial_ss>\d+(?:\.\d+)?)$'
    r'|(?P<count>[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?P<unit>h|min|s|ms)?$'
    r')\s*$'
)


def parse_clock_value(text):
    """Parse an SVG SMIL clock value into seconds.

    Accepted forms: ``"1.5s"``, ``"500ms"``, ``"2min"``, ``"1h"``,
    ``"02:33"``, ``"01:00:00.5"`` and unit-less numbers (seconds).

    :returns: float seconds or `None` if *text* is not a clock value.
    """
    if text is None:
        return None
    match = _CLOCK_RE.match(str(text))
    if match is None:
        return None
    if match.group('full') is not None:
        hours = int(match.group('full')[:2])
        minutes = int(match.group('full_mm'))
        seconds = float(match.group('full_ss'))
        return hours * 3600.0 + minutes * 60.0 + seconds
    if match.group('partial') is not None:
        minutes = int(match.group('partial')[:2])
        seconds = float(match.group('partial_ss'))
        return minutes * 60.0 + seconds
    value = float(match.group('count'))
    unit = match.group('unit')
    return value * _UNIT_FACTORS.get(unit, 1.0) if unit else value


def _split_timing_list(value):
    """Split a begin/end value list on commas/semicolons outside parentheses."""
    tokens = []
    start = 0
    depth = 0
    for pos, char in enumerate(value):
        if char == '(':
            depth += 1
        elif char == ')':
            depth = max(0, depth - 1)
        elif depth == 0 and char in (',', ';'):
            tokens.append(value[start:pos])
            start = pos + 1
    tokens.append(value[start:])
    return [token.strip() for token in tokens if token.strip()]


_ID_VALUE = r'#?[-_a-zA-Z][-\w]*'

_SYNCBASE_RE = re.compile(
    r'^(?P<target>' + _ID_VALUE + r')\.(?P<sync>begin|end)'
    r'(?P<offset>\s*[+-].*)?$'
)
_REPEAT_RE = re.compile(
    r'^(?P<target>' + _ID_VALUE + r')\.repeat\s*\(\s*(?P<iteration>\d+)\s*\)'
    r'(?P<offset>\s*[+-].*)?$'
)
_EVENT_RE = re.compile(
    r'^(?:(?P<target>' + _ID_VALUE + r')\.)?(?P<event>[A-Za-z]+)'
    r'(?P<offset>\s*[+-].*)?$'
)
_ACCESSKEY_RE = re.compile(
    r'^accessKey\s*\(\s*(?P<key>[a-zA-Z])\s*\)(?P<offset>\s*[+-].*)?$'
)
_WALLCLOCK_RE = re.compile(r'^wallclock\s*\(.*\)$')
_OFFSET_RE = re.compile(r'^[+-]?\s*\d')


def _strip_offset(offset):
    if not offset:
        return 0.0, None
    seconds = parse_clock_value(offset.strip())
    return (seconds, None) if seconds is not None else (0.0, offset.strip())


def parse_timing_entry(entry):
    """Parse one begin/end list entry into a structured description.

    :returns: dict with at least a ``kind`` key; ``kind == 'unknown'`` marks
        syntax the parser does not understand.
    """
    text = entry.strip()
    if text == 'indefinite':
        return {'kind': 'indefinite', 'raw': text}
    if _WALLCLOCK_RE.match(text):
        return {'kind': 'wallclock', 'raw': text}

    match = _ACCESSKEY_RE.match(text)
    if match is not None:
        offset, bad = _strip_offset(match.group('offset'))
        return {'kind': 'accesskey', 'key': match.group('key'),
                'offset': offset, 'raw': text,
                'bad_offset': bad}

    match = _REPEAT_RE.match(text)
    if match is not None:
        offset, bad = _strip_offset(match.group('offset'))
        return {'kind': 'repeat', 'target': match.group('target').lstrip('#'),
                'iteration': int(match.group('iteration')),
                'offset': offset, 'raw': text, 'bad_offset': bad}

    match = _SYNCBASE_RE.match(text)
    if match is not None:
        offset, bad = _strip_offset(match.group('offset'))
        return {'kind': 'syncbase', 'target': match.group('target').lstrip('#'),
                'sync': match.group('sync'), 'offset': offset,
                'raw': text, 'bad_offset': bad}

    match = _EVENT_RE.match(text)
    if match is not None and match.group('event') in event_names:
        offset, bad = _strip_offset(match.group('offset'))
        target = match.group('target')
        return {'kind': 'event',
                'target': target.lstrip('#') if target else None,
                'event': match.group('event'), 'offset': offset,
                'raw': text, 'bad_offset': bad}

    if _OFFSET_RE.match(text) and parse_clock_value(text) is not None:
        return {'kind': 'offset', 'offset': parse_clock_value(text),
                'raw': text}

    return {'kind': 'unknown', 'raw': text}


# ---------------------------------------------------------------------------
# findings and report
# ---------------------------------------------------------------------------

class Finding(object):
    """One diagnostic message."""
    __slots__ = ('severity', 'code', 'element', 'element_id',
                 'attribute', 'value', 'message')

    def __init__(self, severity, code, element, attribute, value, message,
                 element_id=None):
        self.severity = severity          # 'error' or 'warning'
        self.code = code
        self.element = element            # element name, e.g. 'animate'
        self.element_id = element_id
        self.attribute = attribute
        self.value = value
        self.message = message

    def __repr__(self):
        where = self.element
        if self.element_id:
            where += '#%s' % self.element_id
        return '%s: %s @ <%s> %s=%r: %s' % (
            self.severity.upper(), self.code, where,
            self.attribute, self.value, self.message)

    def __eq__(self, other):
        if not isinstance(other, Finding):
            return NotImplemented
        return (self.severity, self.code, self.element, self.element_id,
                self.attribute, self.value, self.message) == (
            other.severity, other.code, other.element, other.element_id,
            other.attribute, other.value, other.message)

    def __hash__(self):
        return hash((self.severity, self.code, self.element,
                     self.element_id, self.attribute, self.value,
                     self.message))


class _Node(object):
    """Graph view of one SMIL animation element."""
    __slots__ = ('key', 'element', 'element_id', 'begin_entries',
                 'end_entries', 'edges', 'external', 'start_time',
                 'in_cycle')

    def __init__(self, key, element, element_id):
        self.key = key
        self.element = element
        self.element_id = element_id
        self.begin_entries = []
        self.end_entries = []
        # node -> syncbase/repeat begin targets (other animation nodes)
        self.edges = set()
        self.external = False
        self.start_time = None
        self.in_cycle = False


class AnimationReport(object):
    """Result of :func:`check_animations`."""

    def __init__(self):
        self.findings = []
        self.nodes = {}          # node key -> _Node
        self._id_to_key = {}

    @property
    def ok(self):
        """`True` when no error-severity finding was produced."""
        return not any(f.severity == 'error' for f in self.findings)

    @property
    def errors(self):
        return [f for f in self.findings if f.severity == 'error']

    @property
    def warnings(self):
        return [f for f in self.findings if f.severity == 'warning']

    def codes(self):
        return [f.code for f in self.findings]

    def add(self, severity, code, node_or_element, attribute, value, message,
            element_id=None):
        if isinstance(node_or_element, _Node):
            element = node_or_element.element.elementname
            element_id = node_or_element.element_id
        else:
            element = node_or_element
        self.findings.append(Finding(severity, code, element, attribute,
                                     value, message, element_id))

    def start_times(self):
        """Mapping ``element-id -> earliest start in seconds``.

        Animations driven by external events, taking part in a cycle or with
        an unresolvable timing are omitted (their start time is unknown).
        """
        return {node.element_id: node.start_time
                for node in self.nodes.values()
                if node.element_id and node.start_time is not None}


# ---------------------------------------------------------------------------
# tree traversal and model construction
# ---------------------------------------------------------------------------

def _iter_elements(root):
    stack = [root]
    while stack:
        element = stack.pop()
        yield element
        for child in reversed(getattr(element, 'elements', ())):
            stack.append(child)


def _as_string(value):
    return value if isinstance(value, str) else str(value)


def _build_model(root):
    report = AnimationReport()
    animations = [e for e in _iter_elements(root)
                  if getattr(e, 'elementname', None) in ANIMATION_ELEMENTS]

    # register nodes and element ids present in the whole document
    all_ids = set()
    for index, element in enumerate(animations):
        element_id = element.attribs.get('id')
        key = element_id if element_id is not None else '__anon%d' % index
        node = _Node(key, element, element_id)
        report.nodes[key] = node
        if element_id is not None:
            report._id_to_key[element_id] = key
    for element in _iter_elements(root):
        element_id = getattr(element, 'attribs', {}).get('id')
        if element_id is not None:
            all_ids.add(element_id)

    for node in report.nodes.values():
        for timing_attr, sink in (('begin', node.begin_entries),
                                  ('end', node.end_entries)):
            raw = node.element.attribs.get(timing_attr)
            if raw is None:
                continue
            for token in _split_timing_list(_as_string(raw)):
                parsed = parse_timing_entry(token)
                parsed['attribute'] = timing_attr
                sink.append(parsed)
    return report, all_ids


# ---------------------------------------------------------------------------
# timing reference diagnostics
# ---------------------------------------------------------------------------

def _check_timing_references(report, all_ids):
    for node in report.nodes.values():
        for entry in node.begin_entries + node.end_entries:
            kind = entry['kind']
            attribute = entry['attribute']
            if kind == 'unknown' or entry.get('bad_offset'):
                report.add('error', 'invalid-timing-syntax', node,
                           attribute, entry['raw'],
                           'value is not a valid SMIL timing list entry')
                continue
            target = entry.get('target')
            if target is None:
                if kind in EXTERNAL_KINDS or kind == 'offset':
                    node.external = kind in EXTERNAL_KINDS
                continue

            if target not in all_ids:
                report.add('error', 'missing-timing-reference', node,
                           attribute, entry['raw'],
                           "references unknown element id '%s'" % target)
                continue

            if kind in ('syncbase', 'repeat'):
                target_key = report._id_to_key.get(target)
                if target_key is None:
                    # the id exists, but belongs to a non-animation element
                    report.add('error', 'invalid-timing-reference', node,
                               attribute, entry['raw'],
                               "element '%s' is not an animation element and "
                               "never raises begin/end/repeat events"
                               % target)
                    continue
                if attribute == 'begin':
                    node.edges.add(target_key)
            elif kind == 'event':
                # DOM events may be raised by any element; an existing id is
                # all that is required, and event-driven starts are roots
                node.external = True


def _find_cycles(report):
    """Iterative Tarjan SCC over the begin-reference edges."""
    graph = {key: node.edges for key, node in report.nodes.items()}
    indices = {}
    lowlink = {}
    on_stack = set()
    stack = []
    components = []
    counter = [0]

    for start in graph:
        if start in indices:
            continue
        work = [(start, iter(graph[start]))]
        indices[start] = lowlink[start] = counter[0]
        counter[0] += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, successors = work[-1]
            advanced = False
            for successor in successors:
                if successor not in indices:
                    indices[successor] = lowlink[successor] = counter[0]
                    counter[0] += 1
                    stack.append(successor)
                    on_stack.add(successor)
                    work.append((successor, iter(graph[successor])))
                    advanced = True
                    break
                if successor in on_stack:
                    lowlink[node] = min(lowlink[node], indices[successor])
            if advanced:
                continue
            if lowlink[node] == indices[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])

    cyclic = set()
    for component in components:
        if len(component) > 1 or component[0] in graph[component[0]]:
            cyclic.update(component)
    for key in cyclic:
        report.nodes[key].in_cycle = True
        report.add('error', 'circular-timing-dependency', report.nodes[key],
                   'begin',
                   _as_string(report.nodes[key].element.attribs.get('begin')),
                   'animation waits on a begin event whose own startup '
                   'chain leads back to it')
    return cyclic


def _check_reachability(report, cyclic):
    # reverse edges: target -> elements it can wake up
    reverse = {key: set() for key in report.nodes}
    for key, node in report.nodes.items():
        for target in node.edges:
            reverse[target].add(key)

    # roots: externally triggered, without begin attribute (document begin,
    # i.e. SMIL default begin=0s), or only clock-offset driven
    roots = set()
    for key, node in report.nodes.items():
        if node.external:
            roots.add(key)
            continue
        begin_raw = node.element.attribs.get('begin')
        if begin_raw is None:
            roots.add(key)
            continue
        if node.begin_entries and all(
                entry['kind'] == 'offset' for entry in node.begin_entries):
            roots.add(key)

    reachable = set()
    pending = list(roots)
    while pending:
        key = pending.pop()
        if key in reachable:
            continue
        reachable.add(key)
        pending.extend(reverse[key] - reachable)

    for key, node in report.nodes.items():
        if key in reachable or key in cyclic:
            continue
        report.add('error', 'unreachable-animation', node, 'begin',
                   _as_string(node.element.attribs.get('begin')),
                   'no document/event driven startup chain reaches this '
                   'animation; it can never begin')


# ---------------------------------------------------------------------------
# independent start-time timeline (deterministic fragment)
# ---------------------------------------------------------------------------

def _parse_dur(node):
    raw = node.element.attribs.get('dur')
    if raw is None:
        return None
    text = _as_string(raw)
    if text in ('indefinite', 'media'):
        return None
    return parse_clock_value(text)


def _compute_start_times(report):
    """Earliest deterministic begin time per node, in seconds.

    Independent of the reachability scan: memoized recursive evaluation of
    the SMIL begin rules (document begin == 0 s, syncbase + offset, end
    syncbase == begin + simple duration).  Returns `None` for externally
    driven, cyclic or unresolvable animations.
    """
    nodes = report.nodes
    state = {}          # key -> None (unknown) / float / 'visiting'

    def evaluate(key):
        if key in state:
            return state[key] if state[key] != 'visiting' else None
        state[key] = 'visiting'
        node = nodes[key]
        if node.external:
            state[key] = None
            return None
        entries = node.begin_entries
        if not entries:
            state[key] = 0.0          # SMIL default: document begin
            return 0.0
        candidates = []
        for entry in entries:
            kind = entry['kind']
            if kind == 'offset':
                candidates.append(entry['offset'])
            elif kind == 'syncbase' and entry['target'] in report._id_to_key:
                target = nodes[report._id_to_key[entry['target']]]
                if entry['sync'] == 'begin':
                    base = evaluate(target.key)
                else:
                    target_start = evaluate(target.key)
                    duration = _parse_dur(target)
                    base = (target_start + duration
                            if target_start is not None and duration is not None
                            else None)
                if base is None:
                    candidates = None
                    break
                candidates.append(base + entry['offset'])
            else:
                candidates = None    # repeat/event/...: not deterministic
                break
        result = min(candidates) if candidates else None
        state[key] = result
        node.start_time = result
        return result

    for key in nodes:
        evaluate(key)


# ---------------------------------------------------------------------------
# values / keyTimes / keySplines / dur / repeatCount boundary checks
# ---------------------------------------------------------------------------

def _split_semicolon_list(raw):
    return [item.strip() for item in str(raw).split(';') if item.strip() != '']


def _parse_floats(raw):
    values = []
    for token in _split_semicolon_list(raw):
        try:
            values.append(float(token))
        except ValueError:
            values.append(None)
    return values


def _check_interpolation_attributes(report):
    for node in report.nodes.values():
        element = node.element
        if element.elementname == 'set':
            continue    # <set> has no interpolated values
        _check_values_combinations(node, report)
    for node in report.nodes.values():
        _check_dur_and_repeat(node, report)


def _check_values_combinations(node, report):
    attribs = node.element.attribs
    calc_mode = attribs.get('calcMode')
    has_values = 'values' in attribs and attribs['values'] is not None
    values_count = (len(_split_semicolon_list(attribs['values']))
                    if has_values else None)

    # --- keyTimes ---------------------------------------------------------
    if 'keyTimes' in attribs and attribs['keyTimes'] is not None:
        raw = attribs['keyTimes']
        times = _parse_floats(raw)
        if any(value is None for value in times):
            report.add('error', 'invalid-attribute-combination', node,
                       'keyTimes', raw,
                       'keyTimes must be a semicolon list of numbers')
        else:
            if any(not 0.0 <= value <= 1.0 for value in times):
                report.add('error', 'invalid-attribute-combination', node,
                           'keyTimes', raw,
                           'keyTimes values must be in the range 0..1')
            if any(times[i] >= times[i + 1]
                   for i in range(len(times) - 1)):
                report.add('error', 'invalid-attribute-combination', node,
                           'keyTimes', raw,
                           'keyTimes values must be strictly ascending')
            if times[0] != 0.0 or times[-1] != 1.0:
                report.add('error', 'invalid-attribute-combination', node,
                           'keyTimes', raw,
                           'keyTimes must start with 0 and end with 1')
            if has_values and len(times) != values_count:
                report.add('error', 'invalid-attribute-combination', node,
                           'keyTimes', raw,
                           'keyTimes has %d entries but values has %d; the '
                           'counts must match'
                           % (len(times), values_count))
        if calc_mode == 'paced':
            report.add('warning', 'invalid-attribute-combination', node,
                       'keyTimes', raw,
                       'keyTimes is ignored when calcMode="paced"')

    # --- keySplines -------------------------------------------------------
    if 'keySplines' in attribs and attribs['keySplines'] is not None:
        raw = attribs['keySplines']
        spline_sets = _split_semicolon_list(raw)
        malformed = []
        for spline in spline_sets:
            numbers = [token for token in
                       spline.replace(',', ' ').split() if token]
            if len(numbers) != 4:
                malformed.append(spline)
                continue
            try:
                coords = [float(token) for token in numbers]
            except ValueError:
                malformed.append(spline)
                continue
            if any(not 0.0 <= coord <= 1.0 for coord in coords):
                malformed.append(spline)
        if malformed:
            report.add('error', 'invalid-attribute-combination', node,
                       'keySplines', raw,
                       'each keySplines entry must be exactly 4 numbers in '
                       'the range 0..1')
        if calc_mode != 'spline':
            report.add('warning', 'invalid-attribute-combination', node,
                       'keySplines', raw,
                       'keySplines only takes effect when '
                       'calcMode="spline"')

    if calc_mode == 'spline':
        key_times = attribs.get('keyTimes')
        key_splines = attribs.get('keySplines')
        if key_times is None:
            report.add('error', 'invalid-attribute-combination', node,
                       'calcMode', 'spline',
                       'calcMode="spline" requires a keyTimes attribute')
        elif key_splines is None:
            report.add('error', 'invalid-attribute-combination', node,
                       'keySplines', None,
                       'calcMode="spline" requires a keySplines attribute')
        else:
            interval_count = len(_split_semicolon_list(key_times)) - 1
            spline_count = len(_split_semicolon_list(key_splines))
            if (interval_count >= 0 and spline_count != interval_count
                    and not _has_malformed_spline(key_splines)):
                report.add('error', 'invalid-attribute-combination', node,
                           'keySplines', key_splines,
                           'keySplines needs one control point set per '
                           'keyTimes interval: expected %d, got %d'
                           % (interval_count, spline_count))


def _has_malformed_spline(raw):
    for spline in _split_semicolon_list(raw):
        numbers = spline.replace(',', ' ').split()
        if len(numbers) != 4:
            return True
    return False


def _check_dur_and_repeat(node, report):
    attribs = node.element.attribs

    if 'dur' in attribs and attribs['dur'] is not None:
        raw = attribs['dur']
        text = _as_string(raw)
        if text not in ('indefinite', 'media'):
            seconds = parse_clock_value(text)
            if seconds is None:
                report.add('error', 'invalid-attribute-combination', node,
                           'dur', raw,
                           'dur must be a clock value, "media" or '
                           '"indefinite"')
            elif seconds < 0:
                report.add('error', 'invalid-attribute-combination', node,
                           'dur', raw, 'dur must not be negative')
            # seconds == 0 is a legal zero-duration animation

    if 'repeatCount' in attribs and attribs['repeatCount'] is not None:
        raw = attribs['repeatCount']
        text = _as_string(raw)
        if text != 'indefinite':           # intentional infinite repetition
            try:
                count = float(text)
            except ValueError:
                report.add('error', 'invalid-attribute-combination', node,
                           'repeatCount', raw,
                           'repeatCount must be a non-negative number or '
                           '"indefinite"')
            else:
                if count < 0:
                    report.add('error', 'invalid-attribute-combination',
                               node, 'repeatCount', raw,
                               'repeatCount must not be negative')


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def check_animations(root):
    """Validate all SMIL animation elements inside *root*.

    :param root: a :class:`svgwrite.Drawing` or any svgwrite container
        element
    :returns: :class:`AnimationReport`
    """
    report, all_ids = _build_model(root)
    _check_timing_references(report, all_ids)
    cyclic = _find_cycles(report)
    _check_reachability(report, cyclic)
    _compute_start_times(report)
    _check_interpolation_attributes(report)
    return report
