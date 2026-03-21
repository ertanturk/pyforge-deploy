GITHUB_RELEASE_YAML = """name: PyForge Release

on:
  push:
    tags:
      - 'v*'
      - '[0-9]*.[0-9]*.[0-9]*'
  workflow_dispatch:
    inputs:
      pypi_deploy:
        description: 'Publish package to PyPI'
        required: true
        default: 'true'
        type: choice
        options: ['true', 'false']
      docker_build:
        description: 'Build and push Docker image'
        required: true
        default: 'true'
        type: choice
        options: ['true', 'false']
      bump:
        description: 'Version bump for non-tag dispatch runs'
        required: false
        default: ''
        type: choice
        options:
          - ''
          - 'shame'
          - 'default'
          - 'proud'
          - 'patch'
          - 'minor'
          - 'major'
          - 'alpha'
          - 'beta'
          - 'rc'

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

permissions:
  contents: write
  id-token: write

jobs:
  quality_and_security:
    name: Quality & Security Checks
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - name: Checkout Code
        uses: actions/checkout@v5
        with:
          fetch-depth: 0

      - name: PyForge / Quality + Security
        uses: ertanturk/pyforge-deploy@main
        with:
          python_version: '3.12'
          pypi_deploy: 'false'
          docker_build: 'false'
          bump: ''
          docker_platforms: 'linux/amd64,linux/arm64'
          pyforge_cache: 'true'
          pyforge_ast_cache_ttl: '900'
          pyforge_pypi_cache_ttl: '900'
          run_tests: 'true'
          run_security_scan: 'true'
          target_branch: ${{ github.event.repository.default_branch }}
        env:
          PYFORGE_JSON_LOGS: '1'

  deploy_pypi:
    name: Deploy / PyPI
    needs: [quality_and_security]
    if: >-
      ${{ github.event_name != 'workflow_dispatch' ||
      github.event.inputs.pypi_deploy == 'true' }}
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - name: Checkout Code
        uses: actions/checkout@v5
        with:
          fetch-depth: 0

      - name: PyForge / PyPI Deploy
        uses: ertanturk/pyforge-deploy@main
        with:
          python_version: '3.12'
          pypi_deploy: 'true'
          docker_build: 'false'
          bump: >-
            ${{ github.event_name == 'workflow_dispatch' &&
            github.event.inputs.bump || '' }}
          docker_platforms: 'linux/amd64,linux/arm64'
          pyforge_cache: 'true'
          pyforge_ast_cache_ttl: '900'
          pyforge_pypi_cache_ttl: '900'
          run_tests: 'false'
          run_security_scan: 'false'
          target_branch: ${{ github.event.repository.default_branch }}
        env:
          PYFORGE_JSON_LOGS: '1'

  deploy_docker:
    name: Deploy / Docker
    needs: [quality_and_security]
    if: >-
      ${{ github.event_name != 'workflow_dispatch' ||
      github.event.inputs.docker_build == 'true' }}
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - name: Checkout Code
        uses: actions/checkout@v5
        with:
          fetch-depth: 0

      - name: PyForge / Docker Deploy
        uses: ertanturk/pyforge-deploy@main
        with:
          python_version: '3.12'
          pypi_deploy: 'false'
          docker_build: 'true'
          bump: ''
          docker_platforms: 'linux/amd64,linux/arm64'
          pyforge_cache: 'true'
          pyforge_ast_cache_ttl: '900'
          pyforge_pypi_cache_ttl: '900'
          run_tests: 'false'
          run_security_scan: 'false'
          target_branch: ${{ github.event.repository.default_branch }}
        env:
          PYFORGE_JSON_LOGS: '1'
          DOCKERHUB_USERNAME: ${{ secrets.DOCKERHUB_USERNAME }}
          DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}
"""
