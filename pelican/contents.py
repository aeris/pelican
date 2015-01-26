# -*- coding: utf-8 -*-
import copy
import locale
import logging
import functools
import os
import re

from datetime import datetime
from sys import platform, stdin


from pelican.settings import _DEFAULT_CONFIG
from pelican.utils import slugify, truncate_html_words, memoized
from pelican import signals

logger = logging.getLogger(__name__)


class Page(object):
    """Represents a page
    Given a content, and metadata, create an adequate object.

    :param content: the string to parse, containing the original content.
    """
    mandatory_properties = ('title',)
    default_template = 'page'

    def __init__(self, content, metadata=None, settings=None,
                 filename=None, context=None):
        # init parameters
        if not metadata:
            metadata = {}
        if not settings:
            settings = copy.deepcopy(_DEFAULT_CONFIG)

        self.settings = settings
        self._content = content
        self._context = context
        self.translations = []

        local_metadata = dict(settings.get('DEFAULT_METADATA', ()))
        local_metadata.update(metadata)

        # set metadata as attributes
        for key, value in local_metadata.items():
            setattr(self, key.lower(), value)

        # also keep track of the metadata attributes available
        self.metadata = local_metadata

        #default template if it's not defined in page
        self.template = self._get_template()

        # default author to the one in settings if not defined
        if not hasattr(self, 'author'):
            if 'AUTHOR' in settings:
                self.author = Author(settings['AUTHOR'], settings)

        # manage languages
        self.in_default_lang = True
        if 'DEFAULT_LANG' in settings:
            default_lang = settings['DEFAULT_LANG'].lower()
            if not hasattr(self, 'lang'):
                self.lang = default_lang

            self.in_default_lang = (self.lang == default_lang)

        # create the slug if not existing, from the title
        if not hasattr(self, 'slug') and hasattr(self, 'title'):
            self.slug = slugify(self.title)

        if filename:
            self.filename = filename

        # manage the date format
        if not hasattr(self, 'date_format'):
            if hasattr(self, 'lang') and self.lang in settings['DATE_FORMATS']:
                self.date_format = settings['DATE_FORMATS'][self.lang]
            else:
                self.date_format = settings['DEFAULT_DATE_FORMAT']

        if isinstance(self.date_format, tuple):
            locale.setlocale(locale.LC_ALL, self.date_format[0])
            self.date_format = self.date_format[1]

        if hasattr(self, 'date'):
            encoded_date = self.date.strftime(
                    self.date_format.encode('ascii', 'xmlcharrefreplace'))

            if platform == 'win32':
                self.locale_date = encoded_date.decode(stdin.encoding)
            else:
                self.locale_date = encoded_date.decode('utf')

        # manage status
        if not hasattr(self, 'status'):
            self.status = settings['DEFAULT_STATUS']
            if not settings['WITH_FUTURE_DATES']:
                if hasattr(self, 'date') and self.date > datetime.now():
                    self.status = 'draft'

        # store the summary metadata if it is set
        if 'summary' in metadata:
            self._summary = metadata['summary']

        signals.content_object_init.send(self.__class__, instance=self)

    def check_properties(self):
        """test that each mandatory property is set."""
        for prop in self.mandatory_properties:
            if not hasattr(self, prop):
                raise NameError(prop)

    @property
    def url_format(self):
        return {
            'slug': getattr(self, 'slug', ''),
            'lang': getattr(self, 'lang', 'en'),
            'date': getattr(self, 'date', datetime.now()),
            'author': getattr(self, 'author', ''),
            'category': getattr(self, 'category',
                self.settings['DEFAULT_CATEGORY']),
        }

    def _expand_settings(self, key):
        fq_key = ('%s_%s' % (self.__class__.__name__, key)).upper()
        return self.settings[fq_key].format(**self.url_format)

    def get_url_setting(self, key):
        key = key if self.in_default_lang else 'lang_%s' % key
        return self._expand_settings(key)

    def _update_content(self, content, siteurl):
        """Change all the relative paths of the content to relative paths
        suitable for the ouput content.

        :param content: content resource that will be passed to the templates.
        :param siteurl: siteurl which is locally generated by the writer in
            case of RELATIVE_URLS.
        """
        hrefs = re.compile(r"""
            (?P<markup><\s*[^\>]*  # match tag with src and href attr
                (?:href|src)\s*=)

            (?P<quote>["\'])      # require value to be quoted
            (?P<path>\|(?P<what>.*?)\|(?P<value>.*?))  # the url value
            \2""", re.X)

        def replacer(m):
            what = m.group('what')
            value = m.group('value')
            origin = m.group('path')
            # we support only filename for now. the plan is to support
            # categories, tags, etc. in the future, but let's keep things
            # simple for now.
            if what == 'filename':
                if value.startswith('/'):
                    value = value[1:]
                else:
                    # relative to the filename of this content
                    value = self.get_relative_filename(
                        os.path.join(self.relative_dir, value)
                    )

                if value in self._context['filenames']:
                    origin = self._context['filenames'][value].url
                else:
                    logger.warning(u"Unable to find {fn}, skipping url"
                                    " replacement".format(fn=value))

            return m.group('markup') + m.group('quote') + origin \
                    + m.group('quote')

        return hrefs.sub(replacer, content)

    @memoized
    def get_content(self, siteurl):
        return self._update_content(
                self._get_content() if hasattr(self, "_get_content")
                    else self._content,
                siteurl)

    @property
    def content(self):
        return self.get_content(self._context['localsiteurl'])

    def _get_summary(self):
        """Returns the summary of an article, based on the summary metadata
        if it is set, else truncate the content."""
        if hasattr(self, '_summary'):
            return self._summary
        else:
            if self.settings['SUMMARY_MAX_LENGTH']:
                return truncate_html_words(self.content,
                        self.settings['SUMMARY_MAX_LENGTH'])
            return self.content

    def _set_summary(self, summary):
        """Dummy function"""
        pass

    summary = property(_get_summary, _set_summary, "Summary of the article."
                       "Based on the content. Can't be set")

    url = property(functools.partial(get_url_setting, key='url'))
    save_as = property(functools.partial(get_url_setting, key='save_as'))

    def _get_template(self):
        if hasattr(self, 'template') and self.template is not None:
            return self.template
        else:
            return self.default_template

    def get_relative_filename(self, filename=None):
        """Return the relative path (from the content path) to the given
        filename.

        If no filename is specified, use the filename of this content object.
        """
        if not filename:
            filename = self.filename

        return os.path.relpath(
            os.path.abspath(os.path.join(self.settings['PATH'], filename)),
            os.path.abspath(self.settings['PATH'])
        )

    @property
    def relative_dir(self):
        return os.path.dirname(os.path.relpath(
            os.path.abspath(self.filename),
            os.path.abspath(self.settings['PATH']))
        )


class Article(Page):
    mandatory_properties = ('title', 'date', 'category')
    default_template = 'article'


class Quote(Page):
    base_properties = ('author', 'date')


class URLWrapper(object):
    def __init__(self, name, settings):
        self.name = unicode(name)
        self.slug = slugify(self.name)
        self.settings = settings

    def as_dict(self):
        return self.__dict__

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        return self.name == unicode(other)

    def __str__(self):
        return str(self.name.encode('utf-8', 'replace'))

    def __unicode__(self):
        return self.name

    def _from_settings(self, key, get_page_name=False):
        """Returns URL information as defined in settings. 
        When get_page_name=True returns URL without anything after {slug}
        e.g. if in settings: CATEGORY_URL="cat/{slug}.html" this returns "cat/{slug}"
        Useful for pagination."""
        setting = "%s_%s" % (self.__class__.__name__.upper(), key)
        value = self.settings[setting]
        if not isinstance(value, basestring):
            logger.warning(u'%s is set to %s' % (setting, value))
            return value
        else:
            if get_page_name:
                return unicode(os.path.splitext(value)[0]).format(**self.as_dict())
            else:
                return unicode(value).format(**self.as_dict())

    page_name = property(functools.partial(_from_settings, key='URL', get_page_name=True))
    url = property(functools.partial(_from_settings, key='URL'))
    save_as = property(functools.partial(_from_settings, key='SAVE_AS'))


class Category(URLWrapper):
    pass


class Tag(URLWrapper):
    def __init__(self, name, *args, **kwargs):
        super(Tag, self).__init__(unicode.strip(name), *args, **kwargs)


class Author(URLWrapper):
    pass


class StaticContent(object):
    def __init__(self, src, dst=None, settings=None):
        if not settings:
            settings = copy.deepcopy(_DEFAULT_CONFIG)
        self.src = src
        self.url = dst or src
        self.filepath = os.path.join(settings['PATH'], src)
        self.save_as = os.path.join(settings['OUTPUT_PATH'], self.url)

    def __str__(self):
        return str(self.filepath.encode('utf-8', 'replace'))

    def __unicode__(self):
        return self.filepath


def is_valid_content(content, f):
    try:
        content.check_properties()
        return True
    except NameError, e:
        logger.error(u"Skipping %s: impossible to find informations about"
                      "'%s'" % (f, e))
        return False
