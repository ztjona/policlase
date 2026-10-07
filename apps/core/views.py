from importlib.metadata import PackageNotFoundError, version

from django.conf import settings
from django.shortcuts import render


def about(request):
    try:
        gen_version = version("policlase-gen")
    except PackageNotFoundError:
        gen_version = "?"
    return render(request, "core/about.html", {
        "version": settings.POLICLASE_VERSION, "gen_version": gen_version,
        "build_date": settings.POLICLASE_BUILD_DATE,
    })
