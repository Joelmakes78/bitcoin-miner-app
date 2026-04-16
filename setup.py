from setuptools import setup, find_packages

setup(
    name='bitcoin-miner-app',
    version='0.1.0',
    author='Joelmakes78',
    author_email='joelmakes78@example.com',
    description='A Bitcoin mining application',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    url='https://github.com/Joelmakes78/bitcoin-miner-app',
    packages=find_packages(),
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.6',
    install_requires=[
        'requests',
        'numpy',
        'pandas',
    ],
)