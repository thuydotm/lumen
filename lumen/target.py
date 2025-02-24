import datetime as dt

from functools import partial
from io import BytesIO, StringIO
from itertools import product

import panel as pn
import param

from panel.viewable import Viewer

from .config import _LAYOUTS
from .filters import FacetFilter, Filter
from .panel import IconButton
from .pipeline import Pipeline
from .sources import Source
from .state import state
from .util import extract_refs
from .views import DOWNLOAD_FORMATS, View


class Card(Viewer):
    """
    A Card renders a layout of multiple views given a layout specification.
    """

    layout = param.ClassSelector(default='column', class_=(str, list, dict), doc="""
        Defines the layout of the views in the monitor target. Can be
        'column', 'row', 'grid', 'row' or a nested list of indexes
        corresponding to the views, e.g. [[0, 1], [2]] will create
        a Column of one row containing views 0 and 1 and a second Row
        containing view 2.""" )

    title = param.String(doc="A title for this Card.")

    views = param.ClassSelector(class_=(list, dict), doc="""
        List or dictionary of View objects.""")

    def __init__(self, **params):
        self.kwargs = params.pop('kwargs', {})
        super().__init__(**params)
        params = {k: v for k, v in self.kwargs.items() if k in pn.Card.param}
        self._card = pn.Card(
            title=self.title, name=self.title, collapsible=False, **params
        )
        self._card[:] = [self._construct_layout()]

    def __panel__(self):
        return self._card

    def _construct_layout(self):
        layout = self.layout
        if isinstance(layout, list):
            view_size = len(self.views)
            item = pn.Column(sizing_mode='stretch_both')
            for row_spec in layout:
                row = pn.Row(sizing_mode='stretch_width')
                for index in row_spec:
                    if isinstance(index, int):
                        if index >= view_size:
                            raise ValueError(
                                f"Layout specification for '{self.title}' target references "
                                f"out-of-bounds index ({index}) even though the maximum "
                                f"available index is {view_size - 1}."
                            )
                        view = self.views[index]
                    else:
                        matches = [view for view in self.views if view.name == index]
                        if matches:
                            view = matches[0]
                        else:
                            raise KeyError("Target could not find named "
                                           f"view '{index}'.")
                    row.append(view.panel)
                item.append(row)
        else:
            kwargs = dict(self.kwargs)
            if isinstance(layout, dict):
                layout_kwargs = dict(layout)
                layout = layout_kwargs.pop('type')
                kwargs.update(layout_kwargs)
            if layout == 'grid' and 'ncols' not in kwargs:
                kwargs['ncols'] = 2
            layout_type = _LAYOUTS[layout]
            layout_params = list(layout_type.param.params())
            kwargs = {k: v for k, v in kwargs.items() if k in layout_params}
            item = layout_type(*(view.panel for view in self.views), **kwargs)
        return item

    @classmethod
    def from_spec(cls, spec, filters=None, pipelines={}):
        spec = dict(spec)
        view_specs = spec.get('views', [])
        spec['views'] = views = []
        for view in view_specs:
            if isinstance(view_specs, dict):
                view_spec = dict(view_specs[view], name=view)
            else:
                view_spec = view
            if 'table' in view_spec and view_spec['table'] in pipelines:
                pipeline = pipelines[view_spec['table']]
            elif len(pipelines) == 1:
                pipeline = list(pipelines.values())[0]
                if 'pipeline' in view_spec:
                    del view_spec['pipeline']
            if filters:
                pipeline = pipeline.chain(filters=list(filters))
            view = View.from_spec(view_spec, pipeline=pipeline)
            views.append(view)
        if filters:
            spec['title'] = ' '.join([f'{f.label}: {f.value}' for f in filters])
        return cls(**spec)

    def rerender(self):
        self._card[:] = [self._construct_layout()]


class Facet(param.Parameterized):

    by = param.List(default=[], class_=FacetFilter, doc="""
        Fields to facet by.""")

    layout = param.ClassSelector(default=None, class_=(str, dict), doc="""
        How to lay out the facets.""")

    reverse = param.Boolean(default=False, doc="""
        Whether to reverse the sort order.""")

    sort = param.ListSelector(default=[], objects=[], doc="""
        List of fields to sort by.""")

    def __init__(self, **params):
        super().__init__(**params)
        self._sort_widget = pn.widgets.MultiSelect(
            options=self.param.sort.objects,
            sizing_mode='stretch_width',
            size=len(self.sort),
            value=self.sort,
        )
        self._sort_widget.link(self, value='sort')
        self._reverse_widget = pn.widgets.Checkbox(
            value=self.reverse, name='Reverse', margin=(5, 0, 0, 10)
        )
        self._reverse_widget.link(self, value='reverse')

    def get_sort_key(self, views):
        sort_key = []
        for field in self.sort:
            values = [v.get_value(field) for v in views]
            if values:
                sort_key.append(values[0])
        return tuple(sort_key)

    @param.depends('sort:objects', watch=True)
    def _update_options(self):
        self._sort_widget.options = self.param.sort.objects

    @classmethod
    def from_spec(cls, spec, schema):
        """
        Creates a Facet object from a schema and a set of fields.
        """
        by = []
        for by_spec in spec.pop('by', []):
            if isinstance(by_spec, str):
                f = Filter.from_spec({'type': 'facet', 'field': by_spec}, schema)
            elif isinstance(by_spec, dict):
                f = Filter.from_spec(dict(by_spec, type='facet'), schema)
            elif isinstance(by_spec, Filter):
                f = by_spec
            by.append(f)
        sort = spec.pop('sort', [b.field for b in by])
        sorter = cls(by=by, **spec)
        sorter.param.sort.objects = sort
        return sorter

    @property
    def filters(self):
        return product(*[filt.filters for filt in self.by])


class Download(pn.viewable.Viewer):
    """
    The Download object controls options to download the Source tables
    in a variety of formats via the Dashboard UI.
    """

    labels = param.Dict(default={}, doc="""
        A dictionary to override the Button label for each table.""")

    format = param.ObjectSelector(default=None, objects=DOWNLOAD_FORMATS, doc="""
        The format to download the data in.""")

    kwargs = param.Dict(default={}, doc="""
        Keyword arguments passed to the serialization function, e.g.
        data.to_csv(file_obj, **kwargs).""")

    pipelines = param.Dict(default={}, doc="""
        Dictionary of pipelines indexed by table name.""")

    tables = param.List(default=[], doc="""
        The list of tables to allow downloading.""")

    def __init__(self, **params):
        super().__init__(**params)
        default_table = self.tables[0] if self.tables else None
        button_params = {'filename': f'{default_table}.{self.format}'}
        if default_table in self.labels:
            button_params['label'] = self.labels[default_table]
        self._select_download = pn.widgets.Select(
            name='Select table to download', options=self.tables, value=default_table,
            sizing_mode='stretch_width'
        )
        self._select_download.param.watch(self._update_button, 'value')
        self._download_button = pn.widgets.FileDownload(
            callback=self._table_data, align='end',
            sizing_mode='stretch_width', **button_params
        )
        self._layout = pn.Column(
            pn.pane.Markdown('### Download tables', margin=(0, 5, -10, 5)),
            self._download_button,
            pn.layout.Divider(),
            sizing_mode='stretch_width'
        )
        if len(self.tables) > 1:
            self._layout.insert(1, self._select_download)

    def __panel__(self):
        return self._layout

    def _update_button(self, event):
        params = {'filename': f'{event.new}.{self.format}'}
        if event.new in self.labels:
            params['label'] = self.labels[event.new]
        self._download_button.param.set_param(**params)

    def __bool__(self):
        return self.format is not None

    def _table_data(self):
        if self.format in ('json', 'csv'):
            io = StringIO()
        else:
            io = BytesIO()
        table = self._select_download.value
        data = self._pipelines[table].data
        if self.format == 'csv':
            data.to_csv(io, **self.kwargs)
        elif self.format == 'json':
            data.to_json(io, **self.kwargs)
        elif self.format == 'xlsx':
            data.to_excel(io, **self.kwargs)
        elif self.format == 'parquet':
            data.to_parquet(io, **self.kwargs)
        io.seek(0)
        return io

    @classmethod
    def from_spec(cls, spec, pipelines):
        """
        Creates a Download object from a specification.

        Parameters
        ----------
        spec : dict
            Specification declared as a dictionary of parameter values.
        pipelines: Dict[str, Pipeline]
            A dictionary of Pipeline objects indexed by table name.
        """
        return cls(pipelines=pipelines, **spec)


class Target(param.Parameterized):
    """
    A Target renders the results of a Source query using the defined
    set of filters and views.
    """

    download = param.ClassSelector(class_=Download, default=Download(), doc="""
        The download objects determines whether and how the source tables
        can be downloaded.""")

    facet = param.ClassSelector(class_=Facet, doc="""
        The facet object determines whether and how to facet the cards
        on the target.""")

    layout = param.ClassSelector(default='column', class_=(str, list, dict), doc="""
        Defines the layout of the views in the monitor target. Can be
        'column', 'row', 'grid', 'row' or a nested list of indexes
        corresponding to the views, e.g. [[0, 1], [2]] will create
        a Column of one row containing views 0 and 1 and a second Row
        containing view 2.""" )

    reloadable = param.Boolean(default=True, doc="""
        Whether to allow reloading data target's source using a button.""")

    show_title = param.Boolean(default=True, doc="""
        Whether to show the title in Card headers.""")

    title = param.String(doc="A title for this Target.")

    refresh_rate = param.Integer(default=None, doc="""
        How frequently to refresh the monitor by querying the adaptor.""")

    source = param.ClassSelector(class_=Source, doc="""
        The Source queries the data from some data source.""")

    tsformat = param.String(default="%m/%d/%Y %H:%M:%S")

    views = param.ClassSelector(class_=(list, dict), doc="""
        List or dictionary of View specifications.""")

    _header_format = '<div style="font-size: 1.5em; font-weight: bold;">{header}</div>'

    def __init__(self, **params):
        if 'facet' not in params:
            params['facet'] = Facet()
        self._application = params.pop('application', None)
        self._cache = {}
        self._cb = None
        self._scheduled = False
        self._updates = []
        self._timestamp = pn.pane.HTML(
            align='center', margin=(10, 0), sizing_mode='stretch_width'
        )
        self._pipelines = params.pop('pipelines', {})
        self._pipeline_watchers = {}
        self.kwargs = {k: v for k, v in params.items() if k not in self.param}
        super().__init__(**{k: v for k, v in params.items() if k in self.param})

        # Render content
        self._construct_cards()
        self._cards = self.get_cards()

        # Set up watchers
        self.facet.param.watch(self._resort, ['sort', 'reverse'])
        for view in list(self._cache.values())[0].views:
            view.param.watch(self._schedule_rerender, 'rerender')

    def _resort(self, *events):
        self._rerender(update_views=False)

    def _sync_component(self, component, *events):
        component.param.set_param(**{event.name: event.new for event in events})

    def _construct_cards(self):
        controls, all_transforms = [], []
        linked_views = None
        for facet_filters in self.facet.filters:
            key = tuple(str(f.value) for f in facet_filters)
            self._cache[key] = card = Card.from_spec({
                'kwargs': self.kwargs,
                'layout': self.layout,
                'title': self.title,
                'views': self.views,
            }, filters=facet_filters, pipelines=self._pipelines)
            if linked_views is None:
                for view in card.views:
                    if view.controls:
                        controls.append(view.control_panel)
                    transforms = (
                        view.pipeline.traverse('transforms') +
                        view.pipeline.traverse('sql_transforms')
                    )
                    for transform in transforms:
                        if transform not in all_transforms and transform.controls:
                            controls.append(transform.control_panel)
                            all_transforms.append(transform)
                linked_views = card.views
                continue

            # Only the controls for the first facet is shown so link
            # the other facets to the controls of the first
            for v1, v2 in zip(linked_views, card.views):
                v1.param.watch(partial(self._sync_component, v2), v1.refs)
                for t1, t2 in zip(v1.pipeline.transforms, v2.pipeline.transforms):
                    t1.param.watch(partial(self._sync_component, t2), t1.refs)
                for t1, t2 in zip(v1.pipeline.sql_transforms, v2.pipeline.sql_transforms):
                    t1.param.watch(partial(self._sync_component, t2), t1.refs)
        self._view_controls = pn.Column(*controls, sizing_mode='stretch_width')

    ##################################################################
    # Create UI
    ##################################################################

    def get_cards(self):
        cards = []
        for card in self._cache.values():
            sort_key = self.facet.get_sort_key(card.views)
            if any(view for view in card.views):
                cards.append((sort_key, card))
        if self.facet.sort:
            cards = sorted(cards, key=lambda x: x[0])
            if self.facet.reverse:
                cards = cards[::-1]
        return [card for _, card in cards]

    def get_filter_panel(self, skip=None):
        skip = list(skip or [])
        views = []

        # Variable controls
        global_refs = state.global_refs
        target_refs = [ref.split('.')[1] for ref in self.refs if ref not in global_refs]
        var_panel = state.variables.panel(target_refs)
        if var_panel is not None:
            views.append(var_panel)

        # Source controls
        source_panel = self.source.panel
        if source_panel:
            views.extend([
                self._header_format.format(header='Source'),
                source_panel,
                pn.layout.Divider()
            ])

        # Download controls
        if self.download:
            views.append(self.download)

        # Filter controls
        filters = []
        for pipeline in self._pipelines.values():
            subpipeline = pipeline
            while subpipeline is not None:
                subfilters = []
                for filt in subpipeline.filters:
                    fpanel = filt.panel
                    if filt not in skip and fpanel is not None:
                        subfilters.append(fpanel)
                        skip.append(filt)
                if subfilters:
                    filters.append(subfilters)
                subpipeline = subpipeline.pipeline
        if filters:
            views.append(self._header_format.format(header='Filters'))
            views.extend([filt for sfilters in filters[::-1] for filt in sfilters])

        # Facet controls
        if self.facet.param.sort.objects:
            if views:
                views.append(pn.layout.Divider(margin=0, height=5))
            views.extend([
                self._header_format.format(header='Sort'),
                self.facet._sort_widget,
                self.facet._reverse_widget
            ])

        # View controls
        if self._view_controls:
            views.append(self._view_controls)

        # Reload buttons
        if self.reloadable:
            if views:
                views.append(pn.layout.Divider(margin=0, height=5))
            self._reload_button = IconButton(
                icon='fa-sync', size=18, margin=10
            )
            self._reload_button.on_click(self.update)
            self._timestamp.object = f'Last updated: {dt.datetime.now().strftime(self.tsformat)}'
            reload_panel = pn.Row(
                self._reload_button, self._timestamp, sizing_mode='stretch_width',
                margin=(10, 10, 0, 5)
            )
            views.append(reload_panel)
        return pn.Column(*views, name=self.title, sizing_mode='stretch_width')

    ##################################################################
    # Rendering API
    ##################################################################

    def _schedule_rerender(self, *events):
        for e in events:
            if isinstance(e.obj, View) and e.obj not in self._updates:
                self._updates.append(e.obj)
        if self._scheduled:
            return
        self._scheduled = True
        if pn.state.curdoc:
            pn.state.curdoc.add_next_tick_callback(self._rerender)
        else:
            self._rerender()

    def _rerender_cards(self, cards):
        if self._application:
            self._application._set_loading(self.title)
        for card in cards:
            if any(view in self._updates for view in card.views):
                card.rerender()
        self._updates = []

    def _rerender(self, update_views=True):
        self._scheduled = False
        cards = self.get_cards()
        if update_views:
            self._rerender_cards(cards)
        if cards != self._cards:
            self._cards[:] = cards
            if self._application:
                self._application._render()
        if self._application:
            self._application._layout.loading = False

    ##################################################################
    # Public API
    ##################################################################

    @classmethod
    def from_spec(cls, spec, **kwargs):
        """
        Creates a Target object from a specification. If a Target
        specification references an existing Source or Filter by name.

        Parameters
        ----------
        spec : dict
            Specification declared as a dictionary of parameter values.
        kwargs: dict
            Additional kwargs to pass to the Target

        Returns
        -------
        Resolved and instantiated Target object
        """
        # Resolve source
        spec = dict(spec)
        views = spec.get('views', [])
        pipelines = {}
        source_filters = None
        filter_specs = spec.pop('filters', [])
        view_specs = views if isinstance(views, list) else list(views.values())
        if 'source' in spec:
            source_spec = spec.pop('source', None)
            if isinstance(source_spec, Source):
                source = source_spec
            else:
                source = Source.from_spec(source_spec)
            if isinstance(source_spec, str):
                source_filters = state.filters.get(source_spec)
        else:
            if 'pipeline' in spec:
                pspecs = [spec['pipeline']]
            else:
                pspecs = [vspec.get('pipeline') for vspec in view_specs if 'pipeline' in vspec]
            if len(set(pspecs)) > 1:
                raise ValueError('Views on a target must share the same pipeline.')
            elif not pspecs:
                raise ValueError('Target must declare a source or a pipeline.')
            pipeline_spec = pspecs[0]

            if isinstance(pipeline_spec, str):
                if pipeline_spec not in state.pipelines:
                    raise KeyError(f'{pipeline_spec!r} not found in global pipelines.')
                pipeline = state.pipelines[pipeline_spec]
            else:
                pipeline = Pipeline.from_spec(pipeline_spec)
            source = pipeline.source
            pipelines[pipeline.table] = pipeline

        tables = source.get_tables()

        # Resolve facets
        if 'facet' in spec:
            facet_spec = spec.pop('facet', {})
            schema = source.get_schema()
        else:
            facet_spec, schema = {}, {}

        # Backward compatibility
        if any(fspec.get('type') == 'facet' for fspec in filter_specs):
            raise ValueError(
                "Facetting must be declared via the facet specification of a Target, "
                "specifying filters of type 'facet' is no longer supported"
            )
        for view_spec in view_specs:
            if 'pipeline' in view_spec or 'pipeline' in spec:
                continue
            elif 'table' in view_spec:
                table = view_spec['table']
            elif len(tables) == 1:
                table = tables[0]
            else:
                raise ValueError("View spec did not declare unambiguous table reference.")
            pspec = {'table': table}
            if filter_specs:
                pspec['filters'] = filter_specs
            pipelines[table] = Pipeline.from_spec(pspec, source, source_filters)
        if facet_spec and 'sort' in spec:
            param.main.warning(
                "Cannot declare sort spec and provide a facet spec. "
                "Declare the sort fields on the facet.sort key of "
                "the target instead."
            )
        if 'config' in spec:
            param.main.param.warning(
                "Passing config to a target is deprecated use the "
                "facet.layout key on the target instead."
            )
            facet_spec.update(spec.pop('config'))
        if 'facet_layout' in spec:
            param.main.param.warning(
                "Passing facet_layout to a target is deprecated use "
                "the facet.layout key on the target instead."
            )
            facet_spec['layout'] = spec.pop('facet_layout')

        # Resolve download options
        download_spec = spec.pop('download', {})
        if isinstance(download_spec, str):
            download_spec = {'format': download_spec}
        if 'tables' not in download_spec:
            download_spec['tables'] = list(tables)
        spec['download'] = Download.from_spec(download_spec, pipelines)
        spec['facet'] = Facet.from_spec(facet_spec, schema)
        params = dict(kwargs, **spec)
        return cls(source=source, pipelines=pipelines,  **params)

    @property
    def refs(self):
        refs = []
        for pipeline in self._pipelines.values():
            for ref in pipeline.refs:
                if ref not in refs:
                    refs.append(ref)
        views = self.views
        for spec in (views if isinstance(views, list) else views.values()):
            for ref in extract_refs(spec, 'variables'):
                if ref not in refs:
                    refs.append(ref)
        return refs

    @property
    def panels(self):
        """
        Returns a layout of the rendered View objects on this target.
        """
        if len(self._cards) > 1:
            default = 'flex' if 'flex' in _LAYOUTS else 'grid'
        else:
            default = 'column'
        layout = self.facet.layout or default
        kwargs = dict(name=self.title, margin=0, sizing_mode='stretch_width')
        if isinstance(layout, dict):
            layout_kwargs = dict(layout)
            layout = layout_kwargs.pop('type')
            kwargs.update(layout_kwargs)
        layout_type = _LAYOUTS[layout]
        if layout == 'grid' and 'ncols' not in kwargs:
            kwargs['ncols'] = 3
        if self._cards:
            content = self._cards
            if len(self._cards) == 1 and not self.show_title:
                self._cards[0]._card.param.set_param(
                    hide_header=True, sizing_mode='stretch_width', margin=0
                )
        else:
            content = (
                pn.pane.Alert(
                    '**All views are empty since no data is available. '
                    'Try relaxing the filter constraints.**',
                    sizing_mode='stretch_width'
                ),
            )
        return layout_type(*content, **kwargs)

    @pn.depends('refresh_rate', watch=True)
    def start(self, event=None):
        """
        Starts any periodic callback instantiated on this object.
        """
        refresh_rate = self.refresh_rate if event is None else event.new
        if refresh_rate is None:
            return
        if self._cb:
            self._cb.period = refresh_rate
        else:
            self._cb = pn.state.add_periodic_callback(
                self.update, refresh_rate
            )

    def update(self, *events, clear_cache=True):
        """
        Updates the views on this target by clearing any caches and
        rerendering the views on this Target.
        """
        if clear_cache:
            self.source.clear_cache()
        self._rerender()
        self._timestamp.object = f'Last updated: {dt.datetime.now().strftime(self.tsformat)}'
