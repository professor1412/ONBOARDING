"""Execute Part A with this Python environment and export a standalone HTML copy."""
from pathlib import Path
import os
import sys
import tempfile
import argparse

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache'
for name, folder in [('MPLCONFIGDIR', 'matplotlib'), ('IPYTHONDIR', 'ipython'),
                     ('JUPYTER_RUNTIME_DIR', 'jupyter-runtime')]:
    target = CACHE / folder
    target.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault(name, str(target))
os.environ.setdefault('OMP_NUM_THREADS', '2')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '2')
os.environ.setdefault('MPLBACKEND', 'Agg')

import nbformat
from nbclient import NotebookClient
from nbconvert import HTMLExporter
from jupyter_client import AsyncKernelManager
from jupyter_client.kernelspec import KernelSpec

class LocalPythonKernel(AsyncKernelManager):
    @property
    def kernel_spec(self):
        return KernelSpec(argv=[sys.executable, '-m', 'ipykernel_launcher', '-f',
                                '{connection_file}'], display_name='Part A (local Python)',
                          language='python')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Execute an analysis notebook locally.')
    parser.add_argument('notebook', nargs='?', default='analysis_partA.ipynb')
    args = parser.parse_args()
    path = ROOT / args.notebook
    notebook = nbformat.read(path, as_version=4)
    for i, cell in enumerate(notebook.cells):
        if cell.cell_type == 'code':
            compile(cell.source, f'{path.name}:cell-{i}', 'exec')
    client = NotebookClient(notebook, timeout=900, allow_errors=False,
                            resources={'metadata': {'path': str(ROOT)}},
                            kernel_manager_class=LocalPythonKernel)
    def progress(cell, cell_index):
        if cell.cell_type == 'code':
            print(f'Executing notebook cell {cell_index + 1}/{len(notebook.cells)}', flush=True)
    client.on_cell_start = progress
    client.execute()
    nbformat.validate(notebook)
    # Replace only after successful execution, preserving the last good notebook on error.
    with tempfile.NamedTemporaryFile(mode='w', suffix='.ipynb', dir=ROOT, delete=False) as f:
        nbformat.write(notebook, f)
        tmp = Path(f.name)
    tmp.chmod(path.stat().st_mode & 0o777)
    tmp.replace(path)
    exporter = HTMLExporter(template_name='lab')
    html, _ = exporter.from_notebook_node(notebook)
    output_name = 'part_b' if path.stem.lower().endswith('partb') else 'part_a'
    output = ROOT / 'outputs' / output_name
    output.mkdir(parents=True, exist_ok=True)
    (output / f'{path.stem}.html').write_text(html, encoding='utf-8')
    print(f'Executed {sum(c.cell_type == "code" for c in notebook.cells)} code cells successfully.')
    print(f'Notebook: {path}\nReports: {output}')
