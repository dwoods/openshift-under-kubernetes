"""
Deploys OpenShift Origin to an existing Kubernetes cluster.
"""
from setuptools import find_packages, setup

dependencies = ['click', 'pykube', 'pyyaml']

setup(
    name='openshift-under-kubernetes',
    version='1.2.6',
    url='https://github.com/paralin/openshift-under-kubernetes',
    license='BSD',
    author='Christian Stewart',
    author_email='christian@paral.in',
    description='Deploys OpenShift Origin to an existing Kubernetes cluster.',
    long_description=__doc__,
    packages=find_packages(exclude=['tests']),
    include_package_data=True,
    zip_safe=False,
    platforms='any',
    install_requires=dependencies,
    entry_points={
        'console_scripts': [
            'openshift-under-kubernetes = openshift_under_kubernetes.cli:main',
        ],
    },
    classifiers=[
        # As from http://pypi.python.org/pypi?%3Aaction=list_classifiers
        # 'Development Status :: 1 - Planning',
        #'Development Status :: 2 - Pre-Alpha',
        # 'Development Status :: 3 - Alpha',
        'Development Status :: 4 - Beta',
        # 'Development Status :: 5 - Production/Stable',
        # 'Development Status :: 6 - Mature',
        # 'Development Status :: 7 - Inactive',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',
        'Operating System :: POSIX',
        'Operating System :: MacOS',
        'Operating System :: Unix',
        'Operating System :: Microsoft :: Windows',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 3',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ]
)
