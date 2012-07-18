from util.json_request import expect_json
import json

from django.http import HttpResponse, Http404
from django.contrib.auth.decorators import login_required
from django.core.context_processors import csrf
from django_future.csrf import ensure_csrf_cookie
from django.core.urlresolvers import reverse

from xmodule.modulestore import Location
from xmodule.x_module import ModuleSystem
from github_sync import export_to_github
from static_replace import replace_urls

from mitxmako.shortcuts import render_to_response, render_to_string
from xmodule.modulestore.django import modulestore


# ==== Public views ==================================================

@ensure_csrf_cookie
def signup(request):
    """
    Display the signup form.
    """
    csrf_token = csrf(request)['csrf_token']
    return render_to_response('signup.html', {'csrf': csrf_token})


@ensure_csrf_cookie
def login_page(request):
    """
    Display the login form.
    """
    csrf_token = csrf(request)['csrf_token']
    return render_to_response('login.html', {'csrf': csrf_token})


# ==== Views for any logged-in user ==================================

@login_required
@ensure_csrf_cookie
def index(request):
    """
    List all courses available to the logged in user
    """
    courses = modulestore().get_items(['i4x', None, None, 'course', None])
    return render_to_response('index.html', {
        'courses': [(course.metadata['display_name'],
                    reverse('course_index', args=[
                        course.location.org,
                        course.location.course,
                        course.location.name]))
                    for course in courses]
    })


# ==== Views with per-item permissions================================

def has_access(user, location):
    '''Return True if user allowed to access this piece of data'''
    # TODO (vshnayder): actually check perms
    return user.is_active and user.is_authenticated


@login_required
@ensure_csrf_cookie
def course_index(request, org, course, name):
    """
    Display an editable course overview.

    org, course, name: Attributes of the Location for the item to edit
    """
    location = ['i4x', org, course, 'course', name]
    if not has_access(request.user, location):
        raise Http404  # TODO (vshnayder): better error

    # TODO (cpennington): These need to be read in from the active user
    course = modulestore().get_item(location)
    weeks = course.get_children()
    return render_to_response('course_index.html', {'weeks': weeks})


@login_required
def edit_item(request):
    """
    Display an editing page for the specified module.

    Expects a GET request with the parameter 'id'.

    id: A Location URL
    """
    # TODO (vshnayder): change name from id to location in coffee+html as well.
    item_location = request.GET['id']
    if not has_access(request.user, item_location):
        raise Http404  # TODO (vshnayder): better error

    item = modulestore().get_item(item_location)
    return render_to_response('unit.html', {
        'contents': item.get_html(),
        'js_module': item.js_module_name,
        'category': item.category,
        'name': item.name,
        'previews': get_module_previews(item),
    })


def user_author_string(user):
    '''Get an author string for commits by this user.  Format:
    first last <email@email.com>.

    If the first and last names are blank, uses the username instead.
    Assumes that the email is not blank.
    '''
    f = user.first_name
    l = user.last_name
    if f == '' and l == '':
        f = user.username
    return '{first} {last} <{email}>'.format(first=f,
                                             last=l,
                                             email=user.email)


@login_required
def preview_dispatch(request, module_id, dispatch):
    """
    Dispatch an AJAX action to a preview XModule

    Expects a POST request, and passes the arguments to the module

    module_id: The Location of the module to dispatch to
    dispatch: The action to execute
    """
    pass


def render_from_lms(template_name, dictionary, context=None, namespace='main'):
    """
    Render a template using the LMS MAKO_TEMPLATES
    """
    return render_to_string(template_name, dictionary, context, namespace="lms." + namespace)


def sample_module_system(descriptor):
    """
    Returns a ModuleSystem for the specified descriptor that is specialized for
    rendering module previews.
    """
    return ModuleSystem(
        ajax_url=reverse('preview_dispatch', args=[descriptor.location.url(), '']),
        # TODO (cpennington): Do we want to track how instructors are using the preview problems?
        track_function=lambda x: None,
        filestore=descriptor.system.resources_fs,
        get_module=get_sample_module,
        render_template=render_from_lms,
        debug=True,
        replace_urls=replace_urls
    )


def get_sample_module(location):
    """
    Returns a sample XModule at the specified location. The sample_data is chosen arbitrarily
    from the set of sample data for the descriptor specified by Location

    location: A Location
    """
    descriptor = modulestore().get_item(location)
    instance_state, shared_state = descriptor.get_sample_state()[0]
    system = sample_module_system(descriptor)
    module = descriptor.xmodule_constructor(system)(instance_state, shared_state)
    return module


def get_module_previews(descriptor):
    """
    Returns a list of preview XModule html contents. One preview is returned for each
    pair of states returned by get_sample_state() for the supplied descriptor.

    descriptor: An XModuleDescriptor
    """
    preview_html = []
    system = sample_module_system(descriptor)
    for instance_state, shared_state in descriptor.get_sample_state():
        module = descriptor.xmodule_constructor(system)(instance_state, shared_state)
        preview_html.append(module.get_html())
    return preview_html


@login_required
@expect_json
def save_item(request):
    item_location = request.POST['id']
    if not has_access(request.user, item_location):
        raise Http404  # TODO (vshnayder): better error

    data = json.loads(request.POST['data'])
    modulestore().update_item(item_location, data)

    # Export the course back to github
    # This uses wildcarding to find the course, which requires handling
    # multiple courses returned, but there should only ever be one
    course_location = Location(item_location)._replace(
        category='course', name=None)
    courses = modulestore().get_items(course_location, depth=None)
    for course in courses:
        author_string = user_author_string(request.user)
        export_to_github(course, "CMS Edit", author_string)

    descriptor = modulestore().get_item(item_location)
    preview_html = get_module_previews(descriptor)

    return HttpResponse(json.dumps(preview_html))
