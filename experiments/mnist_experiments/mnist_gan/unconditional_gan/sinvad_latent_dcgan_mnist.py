import torch

# import wandb
import torchvision.transforms as transforms
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
import os

from sa.model import MnistClassifier
from dcgan.dcgan import Generator

# run = wandb.init(project="Sinvad_fitness_beased")
# Set device
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
img_size = 28 * 28 * 1
# Load the trained models
G = Generator(ngpu=1, nz=100, nc=1).to(device)  # Modify the Generator architecture
classifier = MnistClassifier(img_size=img_size).to(device)

# Load the weights of the models
G.load_state_dict(torch.load("./dcgan/weights/netG_epoch_99.pth", map_location=device))
classifier.load_state_dict(
    torch.load(
        "/home/maryam/Documents/SEDL/SINVAD/sa/models/MNIST_conv_classifier.pth",
        map_location=device,
    )
)
# Set models to evaluation mode
G.eval()
classifier.eval()
result_dir = "./result_dcgan_mnist"  # Directory to save the images
# Subdirectory for original images
original_images_dir = os.path.join(result_dir, "original_images")
os.makedirs(result_dir, exist_ok=True)
os.makedirs(original_images_dir, exist_ok=True)


def calculate_fitness(logit, label):
    expected_logit = logit[label]
    # Select the two best indices [perturbed logit]
    best_indices = np.argsort(-logit)[:2]
    best_index1, best_index2 = best_indices
    if best_index1 == label:
        best_but_not_expected = best_index2
    else:
        best_but_not_expected = best_index1
    new_logit = logit[best_but_not_expected]
    # Compute fitness value
    fitness = expected_logit - new_logit
    return fitness


image_info = []
predictions = []
num_samples = 1
gen_num = 500
pop_size = 25
best_left = 10
perturbation_size = 0.05  # Default perturbation size
initial_perturbation_size = 0.01  # Initial perturbation size
# Generate a random latent vector
latent_space = torch.randn(num_samples, 100, 1, 1).to(device)
all_img_lst = []
for i in range(num_samples):
    # Generate the non-perturbed image
    original_latent = latent_space[i].unsqueeze(0)
    original_image = G(original_latent)
    original_logit = classifier(original_image).squeeze().detach().cpu().numpy()
    original_label = np.argmax(original_logit).item()
    # Convert the tensor to a PIL Image
    original_image_pil = transforms.ToPILImage()(original_image.squeeze().cpu())

    # Define the path for saving the original image
    # You can include additional identifiers in the filename if needed, such as a timestamp or iteration number
    original_image_path = os.path.join(
        original_images_dir, f"original_image_{i}_X{original_label}.png"
    )

    # Save the original image
    original_image_pil.save(original_image_path)

    ### Initialize optimization ###
    init_pop = [
        original_latent.unsqueeze(0)
        + initial_perturbation_size * torch.randn_like(original_latent)
        for _ in range(pop_size)
    ]
    now_pop = init_pop
    prev_best = np.inf
    binom_sampler = torch.distributions.binomial.Binomial(
        probs=0.5 * torch.ones(original_latent.size(), device=device)
    )

    ###GA_algorithm###
    for g_idx in range(gen_num):
        indivs_lV = torch.cat(now_pop, dim=0).view(-1, 100, 1, 1)
        Gen_imgs = G(indivs_lV)
        all_logits = classifier(Gen_imgs).squeeze().detach().cpu().numpy()
        fitness_scores = [
            calculate_fitness(all_logits[k_idx], original_label)
            for k_idx in range(pop_size)
        ]
        # Perform selection
        selected_indices = sorted(
            range(len(fitness_scores)), key=lambda i: fitness_scores[i], reverse=True
        )[-best_left:]
        now_best = np.min(fitness_scores)

        parent_pop = [now_pop[i] for i in selected_indices]
        # Perform crossover and mutation
        print("now_best " + str(now_best) + "now_best " + str(np.mean(fitness_scores)))
        # wandb.log({"ft_scores": now_best})
        if now_best < 0:
            break
        elif now_best == prev_best:
            perturbation_size *= 2
        else:
            perturbation_size = initial_perturbation_size

        # Calculate the minimum fitness score across all individuals for the current generation

        k_pop = []
        for k_idx in range(pop_size - best_left):
            mom_idx, pop_idx = np.random.choice(best_left, size=2, replace=False)
            spl_idx = np.random.choice(100, size=1)[0]
            k_gene = torch.cat(
                [parent_pop[mom_idx][:, :spl_idx], parent_pop[pop_idx][:, spl_idx:]],
                dim=1,
            )
            # crossover

            # Mutation
            diffs = (k_gene != original_latent).float()
            k_gene += (
                perturbation_size * torch.randn(k_gene.size()) * diffs
            )  # random adding noise only to diff places
            # random matching to latent_images[i]
            interp_mask = binom_sampler.sample()
            k_gene = interp_mask * original_latent + (1 - interp_mask) * k_gene
            k_pop.append(k_gene)
            # wandb.log({"ft_scores": now_best})
        now_pop = parent_pop + k_pop
        prev_best = now_best

    mod_best = parent_pop[-1].view(-1, 100, 1, 1)
    final_bound_img = G(mod_best)
    # Convert the tensor to a PIL Image
    transform = transforms.ToPILImage()
    perturbed_img_pil = transform(final_bound_img[0].detach().cpu())
    all_img_lst.append(perturbed_img_pil)
    prediction = torch.argmax(classifier(final_bound_img)).item()
    predictions.append(prediction)

    image_path = os.path.join(
        result_dir, f"image_{i}_iteration_{g_idx}_X{original_label}_Y{prediction}.png"
    )
    perturbed_img_pil.save(image_path)

    # Store the image info
    image_info.append((i, g_idx, original_label, prediction))

# Save the images as a numpy array
all_imgs = np.vstack(all_img_lst)
np.save(os.path.join(result_dir, "bound_imgs_mnist_dcgan.npy"), all_imgs)

# Save the image info
with open(os.path.join(result_dir, "image_info.txt"), "w") as f:
    f.write("Image Index, Expected Label X, Predicted Label Y\n")
    for img_info in image_info:
        f.write(f"{img_info[0]}, {img_info[1]}, {img_info[2]}, {img_info[3]}\n")
misclassified_count = 0

# Iterate over the image info list
for img_info in image_info:
    expected_label = img_info[2]
    predicted_label = img_info[3]
    if predicted_label != expected_label:
        misclassified_count += 1

misclassification_percentage = (misclassified_count / len(image_info)) * 100

print(f"Misclassification Percentage: {misclassification_percentage:.2f}%")
