import os
import re

from setuptools import setup, find_packages

v = open(os.path.join(os.path.dirname(__file__), 'sqlalchemy_firebird', '__init__.py'))
VERSION = re.compile(r".*__version__ = '(.*?)'", re.S).match(v.read()).group(1)
v.close()

readme = os.path.join(os.path.dirname(__file__), 'README.md')


setup(name='sqlalchemy_firebird',
      version=VERSION,
      description="Firebird for SQLAlchemy",
      long_description=open(readme).read(),
      url='https://github.com/sqlalchemy/sqlalchemy-firebird',
      author='Paul Graves-DesLauriers',
      author_email='paul@dexmicro.com',
      license='MIT',
      classifiers=[
          # 'Development Status :: 1 - Planning',
          'Development Status :: 2 - Pre-Alpha',
          # 'Development Status :: 3 - Alpha',
          # 'Development Status :: 4 - Beta',
          # 'Development Status :: 5 - Production/Stable',
          'Intended Audience :: Developers',
          'License :: OSI Approved :: MIT License',
          'Programming Language :: Python',
          'Programming Language :: Python :: 3',
          'Programming Language :: Python :: Implementation :: CPython',
          'Topic :: Database :: Front-Ends',
          'Operating System :: OS Independent',
      ],
      keywords='SQLAlchemy Firebird',
      project_urls={
          'Documentation': 'https://github.com/sqlalchemy/sqlalchemy-firebird/wiki',
          'Source': 'https://github.com/sqlalchemy/sqlalchemy-firebird',
          'Tracker': 'https://github.com/sqlalchemy/sqlalchemy-firebird/issues',
      },
      packages=find_packages(include=['sqlalchemy_firebird']),
      include_package_data=True,
      install_requires = ['SQLAlchemy', 'fdb'],
      zip_safe=False,
      entry_points={
          'sqlalchemy.dialects': [
              'firebird2 = sqlalchemy_firebird.fdb:FirebirdDialect_fdb',
          ]
      },
)
